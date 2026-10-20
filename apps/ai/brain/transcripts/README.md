# brain-transcripts

Workstation hook (not deployed to the cluster) that pushes Claude Code and
Codex chat transcripts into the Forgejo brain repo as Markdown:

- `Agents/Claude/<year>/<date> <title> (<id>).md`
- `Agents/Codex/<year>/<date> <title> (<id>).md`

Each note has the user's messages, the agent's replies and a collapsed list of
tool calls. Tool output and hidden reasoning are dropped, and credential-looking
strings (API keys, tokens, JWTs, private keys, license keys, `password=`...) are
redacted before anything leaves the machine. Codex guardian/sub-agent sessions
are skipped.

The hook returns immediately. A detached child waits 2 minutes and pushes only
if no newer turn arrived in the meantime, so an active session produces one
commit shortly after it goes quiet. Claude's `SessionEnd` pushes right away.
Errors go to `~/.local/state/brain-transcripts/log.txt`, never into the session.

## Install on a Mac

```sh
install -m 755 apps/ai/brain/transcripts/brain-transcripts ~/.local/bin/
# Token: brain-sync bot, write:repository on WhiteHouse/brain only
kubectl -n forgejo exec deploy/forgejo -- forgejo admin user generate-access-token \
  --username brain-sync --token-name "transcripts-$(hostname -s)" --scopes write:repository --raw \
  | tail -1 | xargs -I{} security add-generic-password -U -s brain-transcripts -a forgejo -w {}
```

Hooks:

- `~/.claude/settings.json`: `Stop` and `SessionEnd` ->
  `python3 '~/.local/bin/brain-transcripts' hook claude`
- `~/.codex/hooks.json`: `Stop` -> `python3 '~/.local/bin/brain-transcripts' hook codex`
  (Codex asks you to trust a new hook once; approve it in `/hooks`.)

Backfill existing sessions: `brain-transcripts backfill` (`--dry-run` lists,
`--out DIR` renders locally for review without pushing).
