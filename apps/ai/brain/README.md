# brain

Namespace for the second-brain pipeline. The vault itself is the private
Forgejo repo `WhiteHouse/brain` (https://git.internal.white.fm/WhiteHouse/brain).

## brain-sync

`sync/brain_sync.py` (standard-library Python, shipped as a ConfigMap) mirrors
two sources into that repo every 2 minutes:

| Source | Repo path | What |
| --- | --- | --- |
| Meerkat | `Meerkat/People/<Name>.md` | One note per contact: details, relationships (as `[[wikilinks]]`), notes, activities, reminders. |
| Riffado | `Riffado/<year>/<date> <title>.md` | One note per recording: summary, key points, action items, full transcript. |

Each cycle renders every file it owns, compares git blob hashes with the repo,
and pushes only the differences as a single commit through Forgejo's
change-files API (author `brain-sync`). It only ever touches `Meerkat/` and
`Riffado/`; everything else in the repo is yours. Generated files say so at
the top; edit the source app, not the note. A contact or recording deleted at
the source is deleted from the repo.

Riffado recordings are only re-fetched when their `updated_at` or
transcript/summary flags change (tracked in `Riffado/.index.json`), with a full
re-render every 6 hours. `POST /trigger` on the service (port 8080) runs a cycle
immediately, so a Riffado webhook pointed at
`http://brain-sync.brain.svc.cluster.local:8080/trigger` makes new transcripts
show up within seconds instead of within 2 minutes.

### Credentials (`11-secret.sops.yaml`)

- `FORGEJO_TOKEN`: token of the Forgejo user `brain-sync` (restricted account,
  write collaborator on `WhiteHouse/brain` only, scope `write:repository`).
- `MEERKAT_TOKEN`: Meerkat API token (same one meerkat-mcp uses).
- `RIFFADO_TOKEN`: read-only Riffado API key named "brain-sync (Forgejo brain
  repo)"; revoke it under Riffado Settings -> API Keys.

### Ops

```sh
kubectl -n brain logs deploy/brain-sync --tail=50
kubectl -n brain exec deploy/brain-sync -- python3 -c \
  "import urllib.request as u; u.urlopen(u.Request('http://localhost:8080/trigger', method='POST'))"
```

Metrics: `brain_sync_last_success_timestamp_seconds{source}`; alerts
`BrainSyncStale` and `BrainSyncDown` in `cluster-alerts.yml`.
