# obsidian

CouchDB 3.5 backend for the Obsidian **Self-hosted LiveSync** plugin. Internal
only: `https://obsidian.internal.white.fm` (internal gateway listener + a
`SecurityPolicy` allowing RFC1918 and the tailnet's 100.64.0.0/10).

LiveSync settings: URI above, user `admin`, database `obsidian`, password from
`sops -d 11-secret-couchdb-admin.sops.yaml`. Keep End-to-End Encryption on in
the plugin.

## MCP server for agents

`obsidian-mcp` ([es617/obsidian-sync-mcp](https://github.com/es617/obsidian-sync-mcp))
gives agents read/write tools (`search_notes`, `read_note`, `write_note`,
`edit_note`, `move_note`, `delete_note`, `list_*`) directly on the CouchDB
database, at `https://obsidian-mcp.internal.white.fm/mcp`. It uses LiveSync's
own library, so its notes are chunked and E2E-encrypted like the plugin's and
sync to every device. It logs in to CouchDB as `obsidian-mcp`, a non-admin user
that is the only member of `obsidian` (created by `couchdb-init`).

- Secret `13-secret-obsidian-mcp.sops.yaml`: `MCP_AUTH_TOKEN` (bearer token for
  clients), the CouchDB user's password, and the vault's E2EE passphrase and
  optional `sls-id-v1:` recovery code. Set those two with `./set-mcp-passphrase.sh`
  (nothing is echoed). The pod refuses to start without the passphrase.
- Claude Code: `claude mcp add --scope user --transport http obsidian
  https://obsidian-mcp.internal.white.fm/mcp --header "Authorization: Bearer $(sops -d
  13-secret-obsidian-mcp.sops.yaml | awk '/MCP_AUTH_TOKEN:/{print $2}')"`.
  Claude apps that only do OAuth get a password page; the password is the same token.
- No conflict resolution (last write wins), text notes only, and agents can
  delete. The hourly restic snapshots are the undo.

## Backups

restic repo on the Synology: `10.100.1.20:/volume1/longhorn-backups/obsidian-restic`
(beside Longhorn's `backupstore/`), password in `12-secret-restic.sops.yaml`
(keep a copy in 1Password: the backups are useless without it). Snapshots use host `obsidian-couchdb`.

| CronJob | When (America/New_York) | What |
|---|---|---|
| `couchdb-backup` | hourly | `/data` = raw CouchDB files (PVC mounted read-only on couchdb-0's node), `/export` = `_all_docs?include_docs=true&attachments=true` JSON per non-system DB; tag `hourly` |
| `couchdb-backup-prune` | 03:30 | `forget --keep-hourly 48 --keep-daily 14 --keep-weekly 8 --keep-monthly 12 --prune`, then `check` |

If the export can't read CouchDB the job fails before restic runs. If restic
saves a snapshot with unreadable files (exit 3), it is retagged `partial`, so
`latest --tag hourly` never points at one. Alerts: `ObsidianBackupStale` (>3h),
`ObsidianCouchDBNotReady` in `observability/observability/02-config-prometheus.yaml`.

## Restore

`./restic.sh` runs restic in a throwaway pod (the NFS export only admits
cluster nodes) and streams the output back:

```sh
./restic.sh snapshots
./restic.sh dump --archive tar latest --tag hourly /data   > couchdb-data.tar
./restic.sh dump --archive tar latest --tag hourly /export > couchdb-export.tar
RW=1 ./restic.sh ...    # read-write (tag/forget); default is read-only --no-lock
```

On the Synology itself, with Container Manager installed (not yet tested):

```sh
sudo docker run --rm -it -e RESTIC_PASSWORD -v /volume1/longhorn-backups/obsidian-restic:/repo \
  restic/restic:0.19.1 -r /repo --no-lock snapshots
```

Full restore of the raw files into the live StatefulSet:

1. Commit `replicas: 0` in `20-statefulset-couchdb.yaml`, wait for the pod to go.
2. Run a pod that mounts `data-couchdb-0` plus the repo (see the restore pod in
   the initial restore test: `restic restore latest --tag hourly --include /data
   --target /restore` into an emptyDir, then copy `/restore/data/.` onto the PVC
   as uid 5984).
3. Commit `replicas: 1`.

The restore was tested on 2026-10-19 into a scratch namespace: same DBs,
same doc counts, attachments byte-identical.

The `/export` JSON is the format-independent fallback: each file is an
`_all_docs` response, so `jq '{docs: [.rows[].doc]}'` gives a body for
`POST /<db>/_bulk_docs?new_edits=false` into an empty CouchDB (not yet tested;
the raw-file restore above is the tested path).

## Gotcha: Longhorn EIO on node-14

If the volume's iSCSI path breaks (seen when longhorn-manager on node-14
panicked on etcd timeouts), CouchDB returns `eio` and a pod restart does **not**
fix it, because the new pod reuses the stale attachment. Scale to 0 and back via
git. Longhorn may briefly attach the detached volume to another node for an
offline replica rebuild before it attaches to the pod's node again.
