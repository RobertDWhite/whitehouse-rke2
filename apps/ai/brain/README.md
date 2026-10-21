# brain

Namespace for the second-brain pipeline. The vault itself is the private
Forgejo repo `WhiteHouse/brain` (https://git.internal.white.fm/WhiteHouse/brain).

## brain-sync

`sync/brain_sync.py` (standard-library Python, shipped as a ConfigMap) mirrors
three sources into that repo (Meerkat and Riffado every 2 minutes, HubSpot
every 10):

| Source | Repo path | What |
| --- | --- | --- |
| Meerkat | `Meerkat/People/<Name>.md` | One note per contact: details, relationships (as `[[wikilinks]]`), notes, activities, reminders. |
| Riffado | `Riffado/<year>/<date> <title>.md` | One note per recording: summary, key points, action items, full transcript. |
| HubSpot | `<hubspot dir>{Companies,Contacts,Deals}/<Name>.md` | One note per record, linked to each other, with a timeline of logged notes, calls, meetings and tasks. Meeting passwords and dial-in PINs are redacted. |

Each cycle renders every file it owns, compares git blob hashes with the repo,
and pushes only the differences as a single commit through Forgejo's
change-files API (author `brain-sync`). It only ever touches `Meerkat/`,
`Riffado/` and `<hubspot dir>`; everything else in the repo is yours. Generated files say so at
the top; edit the source app, not the note. A contact or recording deleted at
the source is deleted from the repo.

It also publishes in the other direction: each folder `Pages/<site>/` in the
repo is the source of truth for `https://<site>.pages.internal.white.fm/`.
When a folder's git tree changes, brain-sync deploys it through the pages MCP
`deploy_site` tool (replacing the whole site), and it redeploys every 30 minutes
so edits made straight to the pages host don't stick. Removing a folder does not
delete the site. Sites without a folder are untouched and still managed over
MCP.

Recordings can be tied to a business or project with `.brain/routes.json` in
the brain repo (edit it there, no deploy needed):

```json
[{"note": "<folder>/<hub note>",
  "match": ["<regex>"], "ids": ["<riffado id>"],
  "tags": ["<tag>"]}]
```

A recording whose title, summary or transcript matches a `match` regex
(case-insensitive), or whose id is listed, gets a `**Related:**` link to
`note` and the rule's tags. Changing the file re-renders every recording once.

`.brain/corrections.json` fixes names that transcription keeps getting wrong
(`{"<misheard>": "<correct>"}`): whole-word, case-insensitive replacements
applied to recording titles, summaries and transcripts before rendering and
routing.

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
- `PAGES_MCP_TOKEN`: the pages MCP bearer token (same value as
  `apps/ai/pages/11-secret.sops.yaml`).
- `HUBSPOT_TOKEN`: HubSpot service key for the portal (CRM
  read scopes: contacts, companies, deals, notes, calls, meetings, tasks).
  Copy in 1Password vault RKE2, "hubspot service token". Rotate it in HubSpot
  under Development -> Keys -> Service keys.
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

## ww-mirror

CronJob (every 15 min) that mirrors the private GitHub repo
a private GitHub repo into the brain repo under
`<mirror dir>` (script: `ww-mirror/mirror.sh`).

- It checks the upstream head with `git ls-remote` and does nothing unless it
  moved past the commit recorded in `<mirror dir>/.source-commit`.
- On a change it replaces the folder with the upstream tree (minus `.git`,
  `.github` and `.DS_Store`), leaving `HubSpot/` alone (brain-sync renders it;
  see `KEEP_DIRS` in the script), and pushes one commit as `ww-mirror`. Only that
  folder is checked out (sparse, blobless clone), so nothing else in the repo
  is touched.
- GitHub access: read-only deploy key "whitehouse-rke2 brain mirror
  (read-only)" on that repo only (revoke under the repo's Settings -> Deploy
  keys). `ww-mirror/known_hosts` pins github.com's ed25519 host key, checked
  against `api.github.com/meta`.
- Forgejo access: `brain-sync` bot token `ww-mirror`.
- Secret: `12-ww-mirror-secret.sops.yaml` (`deploy-key`, `FORGEJO_TOKEN`).

The folder is a one-way mirror: edit the source repo, not in the
brain repo. Run now: `kubectl -n brain create job --from=cronjob/ww-mirror ww-mirror-manual`.
