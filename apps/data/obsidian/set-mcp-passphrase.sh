#!/bin/sh
# Store the LiveSync E2EE passphrase (and, if the vault has one, the
# "sls-id-v1:..." recovery code) in the MCP Secret without echoing or logging
# them. Then commit and push 13-secret-obsidian-mcp.sops.yaml.
set -eu
cd "$(dirname "$0")"
f=13-secret-obsidian-mcp.sops.yaml
stty -echo; printf 'LiveSync E2EE passphrase: '; IFS= read -r pp; stty echo; echo
[ -n "$pp" ] || { echo "empty passphrase, nothing changed"; exit 1; }
printf '%s' "$pp" | jq -Rs . | sops set --value-stdin "$f" '["stringData"]["COUCHDB_PASSPHRASE"]'
stty -echo; printf 'Recovery code (sls-id-v1:..., Enter if none): '; IFS= read -r rc; stty echo; echo
printf '%s' "$rc" | jq -Rs . | sops set --value-stdin "$f" '["stringData"]["COUCHDB_ID_DERIVATION_KEY"]'
echo "updated $f"
