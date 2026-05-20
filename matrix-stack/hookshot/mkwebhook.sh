#!/usr/bin/env bash
# Create a hookshot generic webhook in a Matrix room and print its URL.
#
# Hookshot v6 removed its provisioning API, so webhook creation has to go through
# Matrix itself (widget UI or bot command). This script automates it by:
#   1) reading synapse's registration_shared_secret (already SOPS-encrypted and
#      mounted into the synapse pod) — no new long-lived credentials introduced
#   2) minting a one-shot server admin via /_synapse/admin/v1/register
#   3) login-as a user already in the target room (defaults to @robert:white.fm)
#   4) writing the uk.half-shot.matrix-hookshot.generic.hook state event directly
#      (no bot command, no message-history pollution, no E2EE decryption needed)
#   5) login-as @webhooks:white.fm to read its room account_data, which is where
#      hookshot stores the generated hookId
#   6) deactivating the temp admin so it doesn't linger
#
# Only stdout output is the webhook URL. All progress/errors go to stderr.
#
# Usage:
#   matrix-stack/hookshot/mkwebhook.sh <room_id> <name> [--as <user_mxid>]
# Example:
#   matrix-stack/hookshot/mkwebhook.sh '!FAYyekTFiXsNeJJlFc:white.fm' fleet
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "usage: $0 <room_id> <name> [--as <user_mxid>]" >&2
  echo "  <room_id>     e.g. '!FAYyekTFiXsNeJJlFc:white.fm'" >&2
  echo "  <name>        webhook name / matrix state_key" >&2
  echo "  --as <mxid>   matrix user to act as (must be a member of the room);" >&2
  echo "                defaults to @robert:white.fm" >&2
  exit 64
fi

export MKW_ROOM="$1"
export MKW_NAME="$2"
export MKW_AS="${4:-@robert:white.fm}"

# Sanity-check the room id shape — refuse anything that doesn't look like one
# so a typo or shell injection attempt fails fast.
if [[ ! "$MKW_ROOM" =~ ^![A-Za-z0-9]+:[A-Za-z0-9.-]+$ ]]; then
  echo "error: room_id must look like '!opaque:server.tld'" >&2
  exit 65
fi
if [[ ! "$MKW_NAME" =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "error: name must match [A-Za-z0-9._-]+" >&2
  exit 65
fi
if [[ ! "$MKW_AS" =~ ^@[A-Za-z0-9._=/+-]+:[A-Za-z0-9.-]+$ ]]; then
  echo "error: --as must be a matrix user id (@local:server)" >&2
  exit 65
fi

# Everything below runs inside the synapse pod, where:
#  - localhost:8008 is the unauthenticated client/admin API listener
#  - /secrets/registration_shared_secret is mounted from the SOPS-encrypted
#    synapse-secrets Secret (never logged, never leaves the pod)
kubectl exec -n matrix -i deploy/synapse -c synapse \
  -- python3 - "$MKW_ROOM" "$MKW_NAME" "$MKW_AS" <<'PYEOF'
import hmac, hashlib, json, secrets, sys, time, urllib.request, urllib.parse, urllib.error

HS = "http://127.0.0.1:8008"
ROOM, NAME, AS_USER = sys.argv[1], sys.argv[2], sys.argv[3]
BOT = "@webhooks:white.fm"
HOOK_EVENT = "uk.half-shot.matrix-hookshot.generic.hook"
URL_PREFIX = "https://webhooks.white.fm/webhook/"

def log(msg):
    print(msg, file=sys.stderr, flush=True)

def req(method, path, token=None, body=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    r = urllib.request.Request(HS + path, method=method, headers=headers)
    if body is not None:
        r.data = json.dumps(body).encode()
    try:
        return json.loads(urllib.request.urlopen(r).read() or b"{}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method} {path} -> {e.code} {e.read().decode()}") from None

# The synapse config has `registration_shared_secret: "/secrets/..."` (the
# literal path string, not the file contents) — there's no `..._path` directive
# in this homeserver.yaml, so synapse uses that string verbatim as the HMAC key.
# Read it from the rendered config so we follow whatever's actually configured.
import re
with open("/config-rendered/homeserver.yaml") as f:
    m = re.search(r'^registration_shared_secret:\s*"([^"]+)"', f.read(), re.M)
if not m:
    print("could not find registration_shared_secret in homeserver.yaml", file=sys.stderr)
    sys.exit(3)
SHARED_SECRET = m.group(1).encode()

# 1. mint a one-shot server admin
admin_user = "mkwebhook-" + secrets.token_hex(4)
admin_pw = secrets.token_urlsafe(32)
nonce = req("GET", "/_synapse/admin/v1/register")["nonce"]
mac_input = nonce.encode() + b"\0" + admin_user.encode() + b"\0" + admin_pw.encode() + b"\0admin"
mac = hmac.new(SHARED_SECRET, mac_input, hashlib.sha1).hexdigest()
admin = req("POST", "/_synapse/admin/v1/register", body={
    "nonce": nonce, "username": admin_user, "password": admin_pw,
    "admin": True, "mac": mac,
})
admin_id = admin["user_id"]
admin_token = admin["access_token"]
log(f"[1/5] temp admin {admin_id} minted")

try:
    # 2. login-as the user that's already in the room
    as_login = req("POST",
        f"/_synapse/admin/v1/users/{urllib.parse.quote(AS_USER)}/login",
        token=admin_token, body={})
    as_token = as_login["access_token"]
    log(f"[2/5] logged in as {AS_USER}")

    # 3. write the hookshot state event directly. hookshot's GenericHook loader
    #    will pick this up, see no hookId mapped in account_data, and mint one.
    room_enc = urllib.parse.quote(ROOM)
    name_enc = urllib.parse.quote(NAME)
    req("PUT",
        f"/_matrix/client/v3/rooms/{room_enc}/state/{HOOK_EVENT}/{name_enc}",
        token=as_token, body={"name": NAME})
    log(f"[3/5] wrote state event {HOOK_EVENT}/{NAME}")

    # 4. login-as the hookshot bot to read its room account_data (where hookshot
    #    stores hookId -> stateKey mappings)
    bot_login = req("POST",
        f"/_synapse/admin/v1/users/{urllib.parse.quote(BOT)}/login",
        token=admin_token, body={})
    bot_token = bot_login["access_token"]

    # 5. poll for up to ~30s for hookshot to ingest the state event
    bot_enc = urllib.parse.quote(BOT)
    acct_path = f"/_matrix/client/v3/user/{bot_enc}/rooms/{room_enc}/account_data/{HOOK_EVENT}"
    hook_id = None
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            data = req("GET", acct_path, token=bot_token)
        except RuntimeError:
            time.sleep(0.5); continue
        hook_id = next((k for k, v in data.items() if v == NAME), None)
        if hook_id:
            break
        time.sleep(0.5)
    if not hook_id:
        log("[5/5] timed out waiting for hookshot to assign a hookId")
        sys.exit(2)
    log(f"[5/5] hookshot assigned hookId {hook_id}")

    print(URL_PREFIX + hook_id)
finally:
    # always deactivate + erase the temp admin so it doesn't accumulate
    try:
        req("POST",
            f"/_synapse/admin/v1/deactivate/{urllib.parse.quote(admin_id)}",
            token=admin_token, body={"erase": True})
        log(f"cleanup: deactivated {admin_id}")
    except Exception as e:
        log(f"cleanup WARNING: could not deactivate {admin_id}: {e}")
PYEOF
