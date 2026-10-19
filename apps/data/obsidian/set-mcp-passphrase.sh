#!/bin/sh
# Store the LiveSync E2EE passphrase (and, if the vault has one, the
# "sls-id-v1:..." recovery code) in the MCP Secret without echoing or logging
# them. Then commit and push 13-secret-obsidian-mcp.sops.yaml.
# Uses `sops edit` with a scripted editor, which works on any sops 3.x; the
# values travel through 0600 temp files, never argv or the environment.
set -eu
cd "$(dirname "$0")"
f=13-secret-obsidian-mcp.sops.yaml
tmp=$(mktemp -d); chmod 700 "$tmp"
trap 'stty echo 2>/dev/null; rm -rf "$tmp"' EXIT INT TERM

stty -echo; printf 'LiveSync E2EE passphrase: '; IFS= read -r pp; stty echo; echo
[ -n "$pp" ] || { echo "empty passphrase, nothing changed"; exit 1; }
stty -echo; printf 'Recovery code (sls-id-v1:..., Enter if none): '; IFS= read -r rc; stty echo; echo
( umask 077; printf '%s' "$pp" > "$tmp/COUCHDB_PASSPHRASE"; printf '%s' "$rc" > "$tmp/COUCHDB_ID_DERIVATION_KEY" )
unset pp rc

cat > "$tmp/editor" <<'PY'
#!/usr/bin/env python3
# argv[1] = decrypted file sops opened; values are read from files beside this script.
import json, os, re, sys
d = os.path.dirname(os.path.abspath(__file__))
path = sys.argv[1]
s = open(path).read()
for key in ("COUCHDB_PASSPHRASE", "COUCHDB_ID_DERIVATION_KEY"):
    val = open(os.path.join(d, key)).read()
    # JSON string literals are valid YAML double-quoted scalars.
    s, n = re.subn(r"(?m)^(\s+" + key + r":).*$", lambda m: m.group(1) + " " + json.dumps(val), s)
    if n != 1:
        sys.exit(f"{key}: expected 1 line, found {n}")
open(path, "w").write(s)
PY
chmod 700 "$tmp/editor"
# `sops edit` arrived in 3.8; older versions open the editor with plain `sops <file>`.
if sops edit --help >/dev/null 2>&1; then
  EDITOR="$tmp/editor" SOPS_EDITOR="$tmp/editor" sops edit "$f"
else
  EDITOR="$tmp/editor" sops "$f"
fi
echo "updated $f"
