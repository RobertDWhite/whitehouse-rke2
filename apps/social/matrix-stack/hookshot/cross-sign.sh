#!/usr/bin/env bash
# Cross-sign the hookshot bot's (@webhooks:white.fm) devices so its messages in
# encrypted rooms aren't flagged "encrypted by a device not verified by its owner".
#
# Hookshot does end-to-bridge encryption with its own device but never signs that
# device with a cross-signing key, and the bot's existing cross-signing keys had no
# private half anywhere. So this script owns the bot's cross-signing identity:
#   - the private seeds (master, self-signing, user-signing) live in
#     hookshot-cross-signing.sops.yaml (not applied to the cluster; only read here)
#   - every @webhooks device not yet signed by the self-signing key gets signed
#
# Run it again whenever hookshot gets a new device (e.g. its crypto store on the
# hookshot-data PVC was reset); already-signed devices are skipped.
#
# --reset  generate a new identity and replace the published one (needed the first
#          time, or if the SOPS file is lost). Element then shows a one-time
#          "identity was reset" notice for the bot in its rooms.
#
# Runs inside the synapse pod and calls the API as the hookshot appservice (its
# as_token from the mounted registration), which is what lets it replace the
# bot's cross-signing keys without interactive auth. Seeds go to the pod on stdin
# and come back on stdout straight into sops; they never touch the working tree
# or the terminal in plaintext.
#
# Usage: matrix-stack/hookshot/cross-sign.sh [--reset]
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOPS_FILE="$DIR/hookshot-cross-signing.sops.yaml"
RESET=0
[[ "${1:-}" == "--reset" ]] && RESET=1

if [[ $RESET -eq 0 ]]; then
  [[ -f "$SOPS_FILE" ]] || { echo "error: $SOPS_FILE missing; run with --reset" >&2; exit 1; }
  SEEDS=$(sops -d --extract '["stringData"]["seeds.json"]' "$SOPS_FILE")
else
  SEEDS='{}'
fi

read -r -d '' PY <<'PYEOF' || true
import json, re, sys, urllib.request, urllib.parse, urllib.error
from signedjson.key import generate_signing_key, get_verify_key, encode_verify_key_base64, \
    encode_signing_key_base64, decode_signing_key_base64
from signedjson.sign import sign_json

HS = "http://127.0.0.1:8008"
BOT = "@webhooks:white.fm"
seeds = json.loads(sys.stdin.read() or "{}")
reset = not seeds

def log(m): print(m, file=sys.stderr, flush=True)

with open("/hookshot-registration/registration.yaml") as f:
    AS_TOKEN = re.search(r'^as_token:\s*"?([^"\s]+)', f.read(), re.M).group(1)

def req(method, path, body=None):
    # Appservice requests masquerade as the bot; requester.app_service_id skips UIA.
    sep = "&" if "?" in path else "?"
    r = urllib.request.Request(f"{HS}{path}{sep}user_id={urllib.parse.quote(BOT)}", method=method,
                               headers={"Content-Type": "application/json", "Authorization": "Bearer " + AS_TOKEN})
    if body is not None: r.data = json.dumps(body).encode()
    try: return json.loads(urllib.request.urlopen(r).read() or b"{}")
    except urllib.error.HTTPError as e: raise RuntimeError(f"{method} {path} -> {e.code} {e.read().decode()}") from None

def key(name):
    # Cross-signing key ids are "ed25519:<public key>", so the version is the public key.
    k = decode_signing_key_base64("ed25519", "x", seeds[name]) if name in seeds else generate_signing_key("x")
    k.version = encode_verify_key_base64(get_verify_key(k))
    seeds[name] = encode_signing_key_base64(k)
    return k

master, ssk, usk = key("master"), key("self_signing"), key("user_signing")
def pub(k, usage):
    return {"user_id": BOT, "usage": [usage], "keys": {f"ed25519:{k.version}": k.version}}

if reset:
    req("POST", "/_matrix/client/v3/keys/device_signing/upload", body={
        "master_key": pub(master, "master"),
        "self_signing_key": sign_json(pub(ssk, "self_signing"), BOT, master),
        "user_signing_key": sign_json(pub(usk, "user_signing"), BOT, master),
    })
    log(f"published new cross-signing keys for {BOT}")

q = req("POST", "/_matrix/client/v3/keys/query", body={"device_keys": {BOT: []}})
published = list((q.get("master_keys", {}).get(BOT) or {}).get("keys", {}).values())
if published != [master.version]:
    raise SystemExit("published master key doesn't match the stored seed; run with --reset")
sid = f"ed25519:{ssk.version}"
todo = {}
for dev, dk in q.get("device_keys", {}).get(BOT, {}).items():
    if sid in dk.get("signatures", {}).get(BOT, {}):
        log(f"device {dev}: already signed")
        continue
    dk.pop("unsigned", None)
    todo[dev] = sign_json(dk, BOT, ssk)
if todo:
    res = req("POST", "/_matrix/client/v3/keys/signatures/upload", body={BOT: todo})
    if res.get("failures"):
        raise SystemExit(f"signature upload failures: {res['failures']}")
    log(f"signed devices: {', '.join(sorted(todo))}")
if reset:
    print(json.dumps(seeds))
PYEOF

OUT=$(printf '%s' "$SEEDS" | kubectl exec -n matrix -i deploy/synapse -c synapse -- python3 -c "$PY")

if [[ $RESET -eq 1 ]]; then
  [[ -n "$OUT" ]] || { echo "error: no seeds returned" >&2; exit 1; }
  TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
  python3 -c 'import json,sys; print(json.dumps({"apiVersion":"v1","kind":"Secret","metadata":{"name":"hookshot-cross-signing","namespace":"matrix"},"type":"Opaque","stringData":{"seeds.json":sys.stdin.read()}}))' \
    <<<"$OUT" >"$TMP/plain.json"
  sops -e --filename-override "$SOPS_FILE" --input-type json --output-type yaml "$TMP/plain.json" >"$SOPS_FILE"
  echo "wrote $SOPS_FILE" >&2
fi
