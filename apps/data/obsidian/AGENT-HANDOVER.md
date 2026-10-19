# Obsidian LiveSync + MCP: agent handover

Updated 2026-10-19. Repo `RobertDWhite/whitehouse-rke2`, path `apps/data/obsidian/`. Argo CD app `obsidian`, which syncs from `main`. Changes are committed straight to `main`.

## Rules (read first)

- **Never print secret values** in chat, logs or command output. Never commit plaintext. Read secrets from SOPS into a shell variable, use it, then `unset` it.
- Don't modify or restart anything outside the `obsidian` namespace without asking the user. Exceptions: the Prometheus obsidian alert rules and the Technitium DNS record below.
- No Claude co-author or "Generated with Claude Code" lines in commits or PRs.
- The kubeconfig points at node-14, which has Longhorn EIO problems. Prefer another control-plane node if kubectl misbehaves.

## What runs

| Thing | Details |
|---|---|
| CouchDB | StatefulSet `couchdb-0`, Service `couchdb.obsidian.svc:5984`. Database `obsidian` holds the vault. |
| LiveSync URL (for the Obsidian plugin) | `https://obsidian.internal.white.fm` (internal HTTPRoute, RFC1918 and 100.64/10 only) |
| MCP server | Deployment `obsidian-mcp` (es617/obsidian-sync-mcp v0.10.0, pinned by digest), 1 replica, Service port 8787 |
| MCP URL | `https://obsidian-mcp.internal.white.fm/mcp` (Streamable HTTP). Its DNS A record (10.99.5.110) is in `platform/networking/technitium/35-zones-secret.sops.yaml`. |
| Backups | Hourly restic CronJob `couchdb-backup`, plus `couchdb-backup-prune`. Repo on the Synology at `/volume1/longhorn-backups/obsidian-restic`. Helper: `restic.sh`. The restore procedure in `README.md` has been tested. |
| Init job | `couchdb-init` (Argo PostSync hook). Creates the databases and the `obsidian-mcp` CouchDB user, and sets `obsidian/_security` so that user is the only member. |

Scheduling: workloads avoid node-10, node-14 and node-50.

## Auth: where every credential lives

Never paste any of these values into chat or logs.

| Secret (k8s, ns `obsidian`) | SOPS file | Keys | Used for |
|---|---|---|---|
| `couchdb-admin` | `11-secret-couchdb-admin.sops.yaml` | `COUCHDB_USER`, `COUCHDB_PASSWORD`, `COUCHDB_SECRET` | CouchDB server admin. The Obsidian plugin on the user's Mac uses these too. |
| `restic` | `12-secret-restic.sops.yaml` | `RESTIC_PASSWORD` | restic repo encryption |
| `obsidian-mcp` | `13-secret-obsidian-mcp.sops.yaml` | `MCP_AUTH_TOKEN` | Bearer token for the MCP endpoint (also accepted on the OAuth password page) |
| | | `COUCHDB_USER` (=`obsidian-mcp`), `COUCHDB_PASSWORD` | Non-admin CouchDB user. Member of the `obsidian` DB only. |
| | | `COUCHDB_PASSPHRASE` | LiveSync E2EE passphrase (set by the user, must match the plugin) |
| | | `COUCHDB_ID_DERIVATION_KEY` | `sls-id-v1:` recovery code, only needed if path obfuscation is on. **Currently empty.** |

Read a value without printing it:

```sh
cd apps/data/obsidian
T=$(sops -d 13-secret-obsidian-mcp.sops.yaml | awk '/MCP_AUTH_TOKEN:/{print $2}' | tr -d '"')
# ... use "$T" ...
unset T
```

Changing the passphrase or recovery code: the user runs `./set-mcp-passphrase.sh` in a real terminal. It prompts without echo and uses `sops edit`, because their sops lacks `set --value-stdin`. Then commit, push, and restart the pod:

```sh
kubectl -n obsidian rollout restart deploy/obsidian-mcp
```

If 1Password copies of the CouchDB admin and restic passwords exist, they may still be pending. `op` needs the user to approve within about 60 seconds.

## Using the MCP

Claude Code has it registered at user scope as `obsidian`, so tools show up as `mcp__obsidian__*` in new sessions. To register it on another machine or client:

```sh
claude mcp add --scope user --transport http obsidian https://obsidian-mcp.internal.white.fm/mcp \
  --header "Authorization: Bearer $(sops -d apps/data/obsidian/13-secret-obsidian-mcp.sops.yaml | awk '/MCP_AUTH_TOKEN:/{print $2}' | tr -d '"')"
```

Tools:

- `read_note`, `write_note`, `edit_note` (operation and old_text), `delete_note`, `move_note`
- `list_notes` (folder, tag, modified_after, sort), `search_notes`, `list_folders`, `list_tags`, `get_note_metadata`

Paths are vault-relative, for example `Inbox/Foo.md`.

Raw HTTP, for scripts or for when the tools aren't loaded:

1. POST `initialize` with headers `Authorization: Bearer $T`, `Content-Type: application/json` and `Accept: application/json, text/event-stream`.
2. Read the `mcp-session-id` response header.
3. Send `notifications/initialized`.
4. Send `tools/call` with an `Mcp-Session-Id` header. Responses are SSE (`data: {...}`).

Limits:

- Last write wins: no merge with edits made in Obsidian at the same time.
- Text notes only.
- It never creates databases.
- The deep links it returns assume the vault is named `obsidian` (`VAULT_NAME` env). Confirm the real vault name with the user.

Optional hardening, if the user asks: set the `READ_ONLY=true` or `WRITE_FOLDERS=Inbox,...` env vars in `90-deployment-mcp.yaml`.

## Verified state (2026-10-19)

- Argo shows `obsidian` Synced and Healthy at `4365a1eb`. The MCP pod is running on node-13 with clean logs: vault ready, search index encrypted, watching changes.
- Endpoint: a request with the token gets 200, a request without it gets 401.
- CouchDB user permissions:
  - `obsidian-mcp` can read the DB.
  - It is denied the config (401) and `_users` (403).
  - Anonymous requests get 401.
- A test note `Inbox/MCP test.md` was written and read back through the MCP. The DB doc_count went from 1 to 3.
- **Not yet confirmed:** that the note decrypts and shows up in Obsidian on the user's Mac.
  - If it is garbled or missing, the vault probably uses path obfuscation. Then the user must supply the recovery code via `./set-mcp-passphrase.sh`.
  - After that, delete the test note and restart the pod.
- Hourly backup jobs completed after the curl-jq v5 image bump.

## Open items

1. The user confirms the test note on the Mac, and then the note is deleted.
2. Confirm the vault name and fix `VAULT_NAME` if needed.
3. Decide on `READ_ONLY` / `WRITE_FOLDERS`.
4. Store credentials in 1Password (needs `op` approval).
5. Possibly move couchdb from node-11 to node-13. node-11 is near its 110-pod limit.
6. Confirm the Prometheus obsidian alert rules are loaded.
7. Remaining node-14 EIO apps, and Longhorn replica eviction before the node-14 wipe.
8. Optional: a Riffado → Obsidian puller that uses this MCP.

## Files

`README.md` covers operations, backup, restore and the MCP section. The other files:

- `90-deployment-mcp.yaml`: Deployment, Service, HTTPRoute, SecurityPolicy, NetworkPolicy
- `60-job-couchdb-init.yaml`: databases, MCP user, `_security`
- `80-`/`81-cronjob-*`: backups
- `set-mcp-passphrase.sh`: sets the E2EE passphrase and recovery code
- `ksops.yaml`: lists the SOPS files
- `kustomization.yaml`: images are pinned by digest
