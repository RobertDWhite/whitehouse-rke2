# brain

Namespace for the second-brain pipeline. The vault itself is the private
Forgejo repo `WhiteHouse/brain` (https://git.internal.white.fm/WhiteHouse/brain).

Nothing in this directory should describe what is *in* that repo. Folder
names, source repos, routing rules and other data-specific settings live in the
SOPS secrets or in the brain repo itself.

## brain-sync

`sync/brain_sync.py` (standard-library Python, shipped as a ConfigMap) renders
three sources into that repo as Markdown: Meerkat and Riffado every 2 minutes,
HubSpot every 10.

| Source | Repo path | What |
| --- | --- | --- |
| Meerkat | `Meerkat/People/<Name>.md` | One note per contact: details, relationships (as `[[wikilinks]]`), notes, activities, reminders. |
| Riffado | `Riffado/<year>/<date> <title>.md` | One note per recording: summary, key points, action items, full transcript. |
| HubSpot | `$HUBSPOT_DIR/{Companies,Contacts,Deals}/<Name>.md` | One note per record, linked to each other, with a timeline of logged notes, calls, meetings and tasks. Meeting passwords and dial-in PINs are redacted. |

It also builds `$ENTITIES_DIR` (default `Entities/`): one page per person,
company, business and property, discovered from the records above, from notes
whose frontmatter has `type: person|company|business|property`, and from the
optional config `.brain/entities.json` in the brain repo (`folder_sources`,
extra `entities`, `aliases`, `merge`, `ignore`, `no_match`,
`min_alias_length`). Each page links its records, related entities and every
note that mentions it (multi-word names, domains and explicitly listed
aliases; ambiguous aliases are skipped). It rebuilds from one repo archive
when `main` moves, at most every `ENTITY_INTERVAL_SECONDS` (600).

Each cycle renders every file it owns, compares git blob hashes with the repo,
and pushes only the differences as a single commit through Forgejo's
change-files API (author `brain-sync`). It only ever touches the folders above and the entities folder;
everything else in the repo is yours. Generated files say so at the top; edit
the source app, not the note. A record deleted at the source is deleted from
the repo.

It also publishes in the other direction: each folder `Pages/<site>/` in the
brain repo is the source of truth for `https://<site>.pages.internal.white.fm/`.
When a folder's git tree changes, brain-sync deploys it through the pages MCP
`deploy_site` tool (replacing the whole site), and it redeploys every 30 minutes
so edits made straight to the pages host don't stick. Removing a folder does not
delete the site; sites without a folder are untouched.

Two optional files in the brain repo (not here) tune how recordings render;
editing either re-renders every recording once:

- `.brain/routes.json`: `[{"note": "<repo path without .md>", "match":
  ["<regex>", ...], "ids": ["<recording id>"], "tags": ["<tag>"]}]`. A recording
  whose title, summary or transcript matches a regex (case-insensitive), or
  whose id is listed, gets a `**Related:**` link to `note` plus the tags.
- `.brain/corrections.json`: `{"<misheard>": "<correct>"}`, whole-word,
  case-insensitive replacements applied to recording titles, summaries and
  transcripts before rendering and routing.

Riffado recordings are only re-fetched when their `updated_at` or
transcript/summary flags change (tracked in `Riffado/.index.json`), with a full
re-render every 6 hours. `POST /trigger` on the service (port 8080) runs a cycle
immediately, so a Riffado webhook pointed at
`http://brain-sync.brain.svc.cluster.local:8080/trigger` makes new transcripts
show up within seconds instead of within 2 minutes.

### Credentials and settings (`11-secret.sops.yaml`)

- `FORGEJO_TOKEN`: token of the Forgejo user `brain-sync` (restricted account,
  write collaborator on `WhiteHouse/brain` only, scope `write:repository`).
- `MEERKAT_TOKEN`: Meerkat API token (same one meerkat-mcp uses).
- `PAGES_MCP_TOKEN`: the pages MCP bearer token (same value as
  `apps/ai/pages/11-secret.sops.yaml`).
- `RIFFADO_TOKEN`: read-only Riffado API key named "brain-sync (Forgejo brain
  repo)"; revoke it under Riffado Settings -> API Keys.
- `HUBSPOT_TOKEN`: HubSpot service key with CRM read scopes (contacts,
  companies, deals, notes, calls, meetings, tasks). Copy in 1Password vault
  RKE2. Rotate it in HubSpot under Development -> Keys -> Service keys.
- `HUBSPOT_DIR`, `HUBSPOT_TAGS`: target folder in the brain repo and extra
  tags (comma-separated) for HubSpot notes.

### Ops

```sh
kubectl -n brain logs deploy/brain-sync --tail=50
kubectl -n brain exec deploy/brain-sync -- python3 -c \
  "import urllib.request as u; u.urlopen(u.Request('http://localhost:8080/trigger', method='POST'))"
```

Metrics: `brain_sync_last_success_timestamp_seconds{source}`; alerts
`BrainSyncStale` and `BrainSyncDown` in `cluster-alerts.yml`.

## brain-mcp

Read-only MCP server over the brain repo, for agents:
`https://brain-mcp.internal.white.fm/mcp` (streamable HTTP, JSON responses,
`Authorization: Bearer <MCP_TOKEN>`). Only `/mcp` is routed through the
gateway (listener `https-internal-white-fm`); `/healthz` stays in-cluster.

- `mcp/git-sync.sh` (sidecar, `alpine/git`) keeps a full clone of the repo in an
  emptyDir and fetches every 60 s. After each new commit it writes
  `/work/meta/HEAD` and a per-file last-commit index (`git log --name-only`).
- `mcp/brain_mcp.py` (stock `python` image, standard library only) reloads every
  Markdown file into memory when `HEAD` changes (well under a second) and serves:
  `search(query, path_prefix?, limit, regex?)`, `read_note(path, max_chars,
  offset)`, `get_entity(name, kind?)` (exact name/alias match against
  `Entities/{People,Companies,Businesses,Properties}/`, case-insensitive, else
  fuzzy candidates), `list_recent(path_prefix?, days, limit, sort=modified|date)`,
  `outline(path)`, `list_dir(path)`, `repo_status()`. No write tools.
- Server instructions point clients at the repo's own `CLAUDE.md`, entity pages
  first, and `[[path]]` citations.

Secret `13-mcp-secret.sops.yaml` (`brain-mcp`):

- `FORGEJO_TOKEN`: token `brain-mcp` of Forgejo user `brain-sync`, scope
  `read:repository` only. Revoke it in Forgejo (as admin, user `brain-sync` ->
  Settings -> Applications) or mint a new one with
  `forgejo admin user generate-access-token --username brain-sync --token-name
  brain-mcp --scopes read:repository --raw`.
- `MCP_TOKEN`: bearer token clients send.

Register with Claude Code:

```sh
sops -d apps/ai/brain/13-mcp-secret.sops.yaml | grep MCP_TOKEN
claude mcp add --transport http --scope user brain \
  https://brain-mcp.internal.white.fm/mcp \
  --header "Authorization: Bearer <MCP_TOKEN>"
```

```sh
kubectl -n brain logs deploy/brain-mcp -c git-sync --tail=20
kubectl -n brain logs deploy/brain-mcp -c brain-mcp --tail=20
```
## brain-briefing

CronJob (06:30 America/New_York) that writes a daily note to
`Briefings/<year>/<date>.md` in the brain repo (script: `briefing/briefing.py`,
standard-library Python) and pushes a short summary with a link to it to ntfy
and a fuller one to the Brain Matrix room (see below).

It downloads one archive of the repo and lists, each item linked to its note:
reminders due or overdue and coming up, birthdays in the next 14 days, open CRM
tasks and deals changed in the last 24h or closing soon (from `$HUBSPOT_DIR`),
action items from recordings of the last 7 days, agent sessions of the last
24h, and the entities with the most new mentions in the last 7 days. CRM items
more than `STALE_DAYS` (30) overdue are counted, not listed. Windows and limits
are env vars at the top of the script.

- Top priorities: optional. `LLM_URL` (OpenAI-compatible base) and `LLM_MODEL`
  point at the shared Ollama router; if unset or failing, the section is left out.
- Calendar and email: not connected yet. `collect_calendar` / `collect_email` in
  the script are the hooks; until they return items the note says so.
- Notification: ntfy topic `NTFY_TOPIC` from `13-briefing-secret.sops.yaml`
  (secret `brain-briefing`). ntfy has no auth, so the topic name is the only
  thing protecting it: keep it random. The push carries counts only, unless
  `NTFY_INCLUDE_PRIORITIES=1`.
- Matrix: `MATRIX_WEBHOOK_URL` (same secret) is the Brain room's Hookshot hook.
  That room is end-to-end encrypted, so its post also lists the priorities and
  the first `MATRIX_ITEMS` (5) calendar entries, due reminders, birthdays,
  closing deals and action items.
- Credentials: `FORGEJO_TOKEN` and `HUBSPOT_DIR` from `brain-sync-env`; commits
  as `brain-sync`. Re-running on the same day rewrites that day's note.

Run now: `kubectl -n brain create job --from=cronjob/brain-briefing briefing-manual`.
Preview without writing: run the script with `DRY_RUN=1` (and optionally
`BRIEFING_DATE=YYYY-MM-DD`).

## Brain Matrix room

Private, end-to-end encrypted room "Brain" (`!izMvHnoMYeLtxUakoz:white.fm`) with
one Hookshot generic hook, `brain`, whose transform is
`apps/social/matrix-stack/hookshot/transforms/brain.js`. Two senders use it:

- A Forgejo push webhook on `WhiteHouse/brain` (repo Settings -> Webhooks,
  type Forgejo, push events, in-cluster URL
  `http://matrix-hookshot.matrix.svc.cluster.local:9000/webhook/<hookId>`).
  Every push becomes one message: commit subjects plus changed notes grouped by
  folder (`Meerkat/People`, `Entities/People`, the repo mirror's folder, hand
  edits...), each linked. Forgejo may only call that host because of
  `FORGEJO__webhook__ALLOWED_HOST_LIST`.
- brain-briefing, every morning (above).

NetworkPolicy `allow-brain-to-hookshot` (platform/policy/network-policies/matrix.yaml)
admits both. The hookId is a bearer credential: rotate it with
`apps/social/matrix-stack/hookshot/rotate-webhook.sh`, then update the Forgejo
webhook and `MATRIX_WEBHOOK_URL`.

## repo-mirror

CronJob (every 15 min) that mirrors a private GitHub repo into a folder of the
brain repo (script: `repo-mirror/mirror.sh`).

- It checks the upstream head with `git ls-remote` and does nothing unless it
  moved past the commit recorded in `$DEST_DIR/.source-commit`.
- On a change it replaces the folder with the upstream tree (minus `.git`,
  `.github` and `.DS_Store`) and pushes one commit as `repo-mirror`. Only that
  folder is checked out (sparse, blobless clone), so nothing else in the repo
  is touched.
- GitHub access: a read-only deploy key on the source repo only (revoke under
  that repo's Settings -> Deploy keys). `repo-mirror/known_hosts` pins
  github.com's ed25519 host key, checked against `api.github.com/meta`.
- Forgejo access: `brain-sync` bot token.
- Secret `12-repo-mirror-secret.sops.yaml`: `deploy-key`, `FORGEJO_TOKEN`,
  `SRC_REPO` (ssh URL), `DEST_DIR` (folder in the brain repo), `MIRROR_LABEL`
  (commit message prefix).

The folder is a one-way mirror: edit the source repo, not the brain repo. Run
now: `kubectl -n brain create job --from=cronjob/repo-mirror repo-mirror-manual`.

## brain-nas-sync

CronJob (every 15 min) that copies the brain repo to the Synology under
`Personal/Brain/` (PV `pv-brain-nas`, NFS like Forgejo's dumps): `brain.git`
is a bare mirror with every ref and the full history, `brain/` a checkout of
`main` for browsing and for the NAS's own backup jobs (e.g. Hyper Backup
off-site). Both are read-only copies; Forgejo stays the source of truth. It
uses brain-sync's Forgejo token. Run now:
`kubectl -n brain create job --from=cronjob/brain-nas-sync brain-nas-sync-manual`.
