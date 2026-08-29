#!/usr/bin/env bash
# Rotate a leaked hookshot generic webhook: mint a replacement in the same room,
# revoke the old hookId, and verify the old URL is dead.
#
# Companion to mkwebhook.sh, which only creates. This one also revokes, because
# a hookshot webhook URL is a bearer credential with no expiry — redacting it
# from a manifest does nothing while the hookId still resolves.
#
# Revocation is three steps, because either one alone is insufficient:
#   1) blank the uk.half-shot.matrix-hookshot.generic.hook state event
#   2) drop the hookId -> state_key mapping from the bot's room account_data
#   3) restart hookshot so it reloads its hook table from scratch
# Step 3 is safe: the crypto store lives on the hookshot-data PVC, so the
# appservice keeps its device identity and Megolm sessions across a restart.
#
# Verification POSTs a marked test payload to the OLD url and requires a
# non-2xx. If revocation failed, that payload lands in the room — which is the
# point: a silent "probably revoked" is worse than one junk message.
#
# Usage:
#   rotate-webhook.sh --old <hook_id> --name <new_state_key> [--room <id>]
#                     [--as <mxid>] [--write <file.sops.yaml>:<key>] [--dry-run]
#
# --room     skip discovery (otherwise the room is found by searching the rooms
#            @webhooks:white.fm is joined to for the old hookId)
# --write    set the new URL on a SOPS-encrypted file's stringData key, edited
#            in place so no plaintext ever hits the working tree. Repeatable.
#
# Only the new hookId prefix goes to stdout; the full URL goes to the SOPS file.
set -euo pipefail

BOT="@webhooks:white.fm"
AS_USER="@robert:white.fm"
URL_PREFIX="https://webhooks.white.fm/webhook/"
OLD_ID=""; NEW_NAME=""; ROOM=""; DRY_RUN=0
WRITE_TARGETS=()

die() { echo "error: $*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --old)     OLD_ID="${2:-}"; shift 2 ;;
    --name)    NEW_NAME="${2:-}"; shift 2 ;;
    --room)    ROOM="${2:-}"; shift 2 ;;
    --as)      AS_USER="${2:-}"; shift 2 ;;
    --write)   WRITE_TARGETS+=("${2:-}"); shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ -n "$OLD_ID"   ]] || die "--old <hook_id> is required"
[[ -n "$NEW_NAME" ]] || die "--name <new_state_key> is required"

# Validate every value that reaches the pod, so a typo fails here rather than
# halfway through a rotation.
[[ "$OLD_ID"   =~ ^[A-Za-z0-9-]+$          ]] || die "--old must be a hook id"
[[ "$NEW_NAME" =~ ^[A-Za-z0-9._-]+$        ]] || die "--name must match [A-Za-z0-9._-]+"
[[ "$AS_USER"  =~ ^@[A-Za-z0-9._=/+-]+:[A-Za-z0-9.-]+$ ]] || die "--as must be a matrix user id"
if [[ -n "$ROOM" ]]; then
  [[ "$ROOM" =~ ^![A-Za-z0-9]+:[A-Za-z0-9.-]+$ ]] || die "--room must look like '!opaque:server.tld'"
fi

command -v kubectl >/dev/null || die "kubectl not found"
command -v sops    >/dev/null || die "sops not found"

for t in "${WRITE_TARGETS[@]:-}"; do
  [[ -z "$t" ]] && continue
  f="${t%%:*}"
  [[ -f "$f" ]] || die "--write target does not exist: $f"
done

PY=$(cat <<'PYEOF'
import hmac, hashlib, json, os, secrets, sys, time, urllib.request, urllib.parse, urllib.error

HS = "http://127.0.0.1:8008"
OLD_ID, NEW_NAME, ROOM, AS_USER, DRY = sys.argv[1:6]
DRY = DRY == "1"
BOT = "@webhooks:white.fm"
HOOK_EVENT = "uk.half-shot.matrix-hookshot.generic.hook"

def log(m): print(m, file=sys.stderr, flush=True)

def req(method, path, token=None, body=None, raw=False):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    r = urllib.request.Request(HS + path, method=method, headers=headers)
    if body is not None:
        r.data = json.dumps(body).encode()
    try:
        resp = urllib.request.urlopen(r)
        data = resp.read() or b"{}"
        return (resp.status, json.loads(data)) if raw else json.loads(data)
    except urllib.error.HTTPError as e:
        if raw:
            return (e.code, {})
        raise RuntimeError(f"{method} {path} -> {e.code} {e.read().decode()}") from None

with open("/config-rendered/homeserver.yaml") as f:
    import re
    m = re.search(r'^registration_shared_secret:\s*"([^"]+)"', f.read(), re.M)
if not m:
    log("could not read registration_shared_secret"); sys.exit(3)
SHARED_SECRET = m.group(1).encode()

admin_user = "rotatehook-" + secrets.token_hex(4)
admin_pw   = secrets.token_urlsafe(32)
nonce = req("GET", "/_synapse/admin/v1/register")["nonce"]
mac_in = nonce.encode() + b"\0" + admin_user.encode() + b"\0" + admin_pw.encode() + b"\0admin"
mac = hmac.new(SHARED_SECRET, mac_in, hashlib.sha1).hexdigest()
admin = req("POST", "/_synapse/admin/v1/register", body={
    "nonce": nonce, "username": admin_user, "password": admin_pw,
    "admin": True, "mac": mac})
admin_id, admin_token = admin["user_id"], admin["access_token"]
log(f"[1/6] temp admin {admin_id} minted")

def login_as(mxid):
    return req("POST", f"/_synapse/admin/v1/users/{urllib.parse.quote(mxid)}/login",
               token=admin_token, body={})["access_token"]

try:
    bot_token = login_as(BOT)
    bot_enc = urllib.parse.quote(BOT)

    def acct_path(room):
        return (f"/_matrix/client/v3/user/{bot_enc}/rooms/"
                f"{urllib.parse.quote(room)}/account_data/{HOOK_EVENT}")

    # --- locate the room holding OLD_ID -------------------------------------
    if ROOM:
        rooms = [ROOM]
    else:
        rooms = req("GET", f"/_synapse/admin/v1/users/{bot_enc}/joined_rooms",
                    token=admin_token)["joined_rooms"]
        log(f"[2/6] searching {len(rooms)} rooms {BOT} is joined to")

    target_room, old_state_key, acct = None, None, {}
    for r in rooms:
        try:
            data = req("GET", acct_path(r), token=bot_token)
        except RuntimeError:
            continue
        if OLD_ID in data:
            target_room, old_state_key, acct = r, data[OLD_ID], data
            break

    if not target_room:
        log(f"[2/6] hookId {OLD_ID} not found in any room "
            f"(already revoked, or wrong id)")
        sys.exit(4)
    log(f"[2/6] found {OLD_ID} -> state_key '{old_state_key}' in {target_room}")

    if DRY:
        print(json.dumps({"room": target_room, "old_state_key": old_state_key,
                          "dry_run": True}))
        sys.exit(0)

    room_enc = urllib.parse.quote(target_room)
    as_token = login_as(AS_USER)

    # --- 1. create the replacement -------------------------------------------
    req("PUT", f"/_matrix/client/v3/rooms/{room_enc}/state/{HOOK_EVENT}/"
               f"{urllib.parse.quote(NEW_NAME)}",
        token=as_token, body={"name": NEW_NAME})
    log(f"[3/6] wrote state event {HOOK_EVENT}/{NEW_NAME}")

    new_id, deadline = None, time.time() + 45
    while time.time() < deadline:
        try:
            data = req("GET", acct_path(target_room), token=bot_token)
        except RuntimeError:
            time.sleep(0.5); continue
        new_id = next((k for k, v in data.items()
                       if v == NEW_NAME and k != OLD_ID), None)
        if new_id:
            break
        time.sleep(0.5)
    if not new_id:
        log("[3/6] timed out waiting for hookshot to assign a new hookId")
        sys.exit(2)
    log(f"[4/6] hookshot assigned new hookId {new_id[:8]}...")

    # --- 2. revoke the old ----------------------------------------------------
    req("PUT", f"/_matrix/client/v3/rooms/{room_enc}/state/{HOOK_EVENT}/"
               f"{urllib.parse.quote(old_state_key)}",
        token=as_token, body={})
    log(f"[5/6] blanked state event {HOOK_EVENT}/{old_state_key}")

    remaining = {k: v for k, v in acct.items() if k != OLD_ID}
    remaining[new_id] = NEW_NAME
    req("PUT", acct_path(target_room), token=bot_token, body=remaining)
    log(f"[5/6] dropped {OLD_ID} from {BOT} room account_data")

    print(json.dumps({"room": target_room, "new_hook_id": new_id,
                      "old_state_key": old_state_key}))
finally:
    try:
        req("POST", f"/_synapse/admin/v1/deactivate/{urllib.parse.quote(admin_id)}",
            token=admin_token, body={"erase": True})
        log(f"[6/6] deactivated {admin_id}")
    except Exception as e:
        log(f"[6/6] WARNING: could not deactivate {admin_id}: {e}")
PYEOF
)

# Pass the script as an argv-encoded blob rather than on stdin: some runners
# (Claude Code's Bash tool among them) close stdin, and `kubectl exec -i` then
# gets an instant EOF and silently runs an empty program.
B64=$(printf '%s' "$PY" | base64 | tr -d '\n')

RESULT=$(kubectl exec -n matrix deploy/synapse -c synapse -- \
  sh -c 'echo "$1" | base64 -d > /tmp/rotate.py; trap "rm -f /tmp/rotate.py" EXIT; python3 /tmp/rotate.py "$2" "$3" "$4" "$5" "$6"' \
  _ "$B64" "$OLD_ID" "$NEW_NAME" "$ROOM" "$AS_USER" "$DRY_RUN")

if [[ "$DRY_RUN" == "1" ]]; then
  echo "$RESULT"
  exit 0
fi

NEW_ID=$(printf '%s' "$RESULT" | python3 -c 'import json,sys; print(json.load(sys.stdin)["new_hook_id"])')
NEW_URL="${URL_PREFIX}${NEW_ID}"

# --- 3. reload hookshot so the revoked hookId leaves its in-memory table ------
echo "restarting hookshot to drop the revoked hookId..." >&2
kubectl rollout restart -n matrix deploy/matrix-hookshot >/dev/null
kubectl rollout status  -n matrix deploy/matrix-hookshot --timeout=180s >/dev/null

# --- 4. write the new URL into the SOPS files --------------------------------
for t in "${WRITE_TARGETS[@]:-}"; do
  [[ -z "$t" ]] && continue
  file="${t%%:*}"; key="${t##*:}"
  # `sops set` edits the encrypted file in place. Decrypting to a temp file and
  # re-encrypting would leave the plaintext URL in the working tree if the
  # re-encrypt failed partway.
  sops set "$file" "[\"stringData\"][\"${key}\"]" "\"${NEW_URL}\""
  sops -d "$file" | grep -q "$NEW_ID" \
    || die "wrote ${file} but could not read the new URL back"
  echo "wrote new URL into ${file} (${key})" >&2
done

# --- 5. prove the old URL is dead --------------------------------------------
echo "verifying old hookId is revoked..." >&2
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST \
  -H 'Content-Type: application/json' \
  -d '{"text":"revocation check - if you see this, the old webhook still works"}' \
  "${URL_PREFIX}${OLD_ID}" || echo "000")

if [[ "$CODE" =~ ^2 ]]; then
  echo "FAILED: old hookId ${OLD_ID} still accepts posts (HTTP ${CODE})." >&2
  echo "        A test message was delivered to the room. Revoke by hand." >&2
  exit 1
fi

echo "old hookId revoked (HTTP ${CODE}); new hookId ${NEW_ID:0:8}..." >&2
