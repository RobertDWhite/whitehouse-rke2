#!/usr/bin/env bash
set -euo pipefail

NS="${NS:-homarr}"
DEPLOYMENT="${DEPLOYMENT:-homarr}"
MARKER_FILE="${MARKER_FILE:-/appdata/db/.persist-check}"
TIMEOUT="${TIMEOUT:-300s}"

get_counts() {
  kubectl -n "$NS" exec "deploy/$DEPLOYMENT" -- sh -lc "node -e 'const Database=require(\"better-sqlite3\");const db=new Database(\"/appdata/db/db.sqlite\",{readonly:true});const tables=[\"user\",\"board\",\"item\",\"integration\"];console.log(tables.map(t=>t+\":\"+db.prepare(\"select count(*) c from \\\"\"+t+\"\\\"\").get().c).join(\",\"));'"
}

MARKER_TOKEN="codex-$(date -u +%Y%m%dT%H%M%SZ)-$RANDOM"

echo "Writing marker token to PVC: $MARKER_TOKEN"
kubectl -n "$NS" exec "deploy/$DEPLOYMENT" -- sh -lc "echo '$MARKER_TOKEN' > '$MARKER_FILE' && cat '$MARKER_FILE'"

BEFORE_COUNTS="$(get_counts)"
echo "Counts before restart: $BEFORE_COUNTS"

echo "Restarting deployment/$DEPLOYMENT..."
kubectl -n "$NS" rollout restart "deployment/$DEPLOYMENT"
kubectl -n "$NS" rollout status "deployment/$DEPLOYMENT" --timeout="$TIMEOUT"

AFTER_TOKEN="$(kubectl -n "$NS" exec "deploy/$DEPLOYMENT" -- sh -lc "cat '$MARKER_FILE'")"
AFTER_COUNTS="$(get_counts)"

echo "Counts after restart:  $AFTER_COUNTS"
echo "Marker after restart:  $AFTER_TOKEN"

if [[ "$AFTER_TOKEN" != "$MARKER_TOKEN" ]]; then
  echo "FAIL: marker token changed or missing after restart."
  exit 1
fi

if [[ "$AFTER_COUNTS" != "$BEFORE_COUNTS" ]]; then
  echo "FAIL: table counts changed after restart."
  exit 1
fi

echo "PASS: Homarr data persistence verified across restart."
