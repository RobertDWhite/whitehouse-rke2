#!/usr/bin/env bash
# Migrate observability/grafana-postgres from Postgres 17 → 18.
#
# Usage:
#   ./migrate-grafana-postgres-17-to-18.sh dump
#   ./migrate-grafana-postgres-17-to-18.sh cutover
#   ./migrate-grafana-postgres-17-to-18.sh cutover --apply-local
#   ./migrate-grafana-postgres-17-to-18.sh restore ./grafana-postgres-17.dump
#
# Requires: kubectl context for the whitehouse cluster.
# See ../28c-MIGRATE-postgres-17-to-18.md
set -euo pipefail

NS=observability
POD=grafana-postgres-0
STS=grafana-postgres
PVC=data-grafana-postgres-0
PGUSER=grafana
DB=grafana
DUMP_LOCAL="${DUMP_LOCAL:-./grafana-postgres-17.dump}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OBS_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

log() { printf '==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

need_kubectl() {
  command -v kubectl >/dev/null || die "kubectl not found"
  kubectl -n "$NS" get sts "$STS" >/dev/null || die "StatefulSet ${NS}/${STS} not found"
}

server_version() {
  kubectl -n "$NS" exec "$POD" -- psql -U "$PGUSER" -d "$DB" -Atc 'SHOW server_version;' 2>/dev/null || true
}

wait_pg_ready() {
  local timeout="${1:-300}"
  local i=0
  log "Waiting for ${POD} Ready (timeout ${timeout}s)..."
  while (( i < timeout )); do
    if kubectl -n "$NS" get pod "$POD" -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null | grep -q True; then
      if kubectl -n "$NS" exec "$POD" -- pg_isready -U "$PGUSER" -d "$DB" >/dev/null 2>&1; then
        log "Postgres ready: $(server_version)"
        return 0
      fi
    fi
    sleep 3
    i=$((i + 3))
  done
  die "Timed out waiting for ${POD}"
}

cmd_dump() {
  need_kubectl
  wait_pg_ready 60
  local ver
  ver="$(server_version)"
  log "Dumping ${DB} from ${POD} (server ${ver}) → ${DUMP_LOCAL}"
  kubectl -n "$NS" exec "$POD" -- \
    pg_dump -U "$PGUSER" -d "$DB" -Fc -f /tmp/grafana.dump
  kubectl -n "$NS" cp "${NS}/${POD}:/tmp/grafana.dump" "$DUMP_LOCAL"
  kubectl -n "$NS" exec "$POD" -- rm -f /tmp/grafana.dump
  ls -lh "$DUMP_LOCAL"
  log "Dump complete."
}

apply_local_manifests() {
  log "Applying local StatefulSet + image tag 18-alpine from ${OBS_DIR}"
  kubectl apply -f "${OBS_DIR}/28a-statefulset-grafana-postgres.yaml"
  kubectl -n "$NS" set image "sts/${STS}" postgres=postgres:18-alpine
}

cmd_wipe_and_recreate() {
  need_kubectl
  log "Deleting pod ${POD} and PVC ${PVC} so PG18 can init a fresh data dir"
  kubectl -n "$NS" delete pod "$POD" --ignore-not-found=true --wait=false || true
  for _ in $(seq 1 60); do
    kubectl -n "$NS" get pod "$POD" >/dev/null 2>&1 || break
    sleep 2
  done
  kubectl -n "$NS" delete pvc "$PVC" --wait=true --ignore-not-found=true
  log "Waiting for STS to recreate pod + PVC..."
  wait_pg_ready 300
  local ver
  ver="$(server_version)"
  case "$ver" in
    18.*) log "Confirmed Postgres ${ver}" ;;
    *)
      die "Expected Postgres 18.x after cutover, got '${ver}'. Is the 18 image rolled out?"
      ;;
  esac
}

cmd_restore() {
  local dump_file="${1:-$DUMP_LOCAL}"
  [[ -f "$dump_file" ]] || die "Dump file not found: ${dump_file}"
  need_kubectl
  wait_pg_ready 120
  log "Copying dump into pod and restoring (pg_restore --clean --if-exists)"
  kubectl -n "$NS" cp "$dump_file" "${NS}/${POD}:/tmp/grafana.dump"
  # pg_restore can exit non-zero on benign notices; verify table count after.
  kubectl -n "$NS" exec "$POD" -- \
    pg_restore -U "$PGUSER" -d "$DB" --clean --if-exists --no-owner \
    /tmp/grafana.dump || true
  local tables
  tables="$(kubectl -n "$NS" exec "$POD" -- \
    psql -U "$PGUSER" -d "$DB" -Atc \
    "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';")"
  log "Public tables after restore: ${tables}"
  (( tables > 10 )) || die "Restore looks incomplete (${tables} tables)"
  kubectl -n "$NS" exec "$POD" -- rm -f /tmp/grafana.dump
  log "Restarting Grafana to reconnect"
  kubectl -n "$NS" rollout restart deployment/grafana
  kubectl -n "$NS" rollout status deployment/grafana --timeout=3m
  log "Restore complete."
}

cmd_cutover() {
  local apply_local=0
  for arg in "$@"; do
    case "$arg" in
      --apply-local) apply_local=1 ;;
    esac
  done

  need_kubectl
  log "Current server version: $(server_version || echo unknown)"

  if [[ ! -f "$DUMP_LOCAL" ]]; then
    cmd_dump
  else
    log "Reusing existing dump ${DUMP_LOCAL}"
  fi

  if (( apply_local )); then
    apply_local_manifests
  else
    log "Assuming this PR is already merged/synced (postgres:18-alpine + new mount)."
    log "If not, re-run with: $0 cutover --apply-local"
  fi

  cmd_wipe_and_recreate
  cmd_restore "$DUMP_LOCAL"
  log "Cutover finished. Keep ${DUMP_LOCAL} until you confirm Grafana looks good."
}

usage() {
  sed -n '2,12p' "$0"
}

main() {
  local cmd="${1:-}"
  shift || true
  case "$cmd" in
    dump) cmd_dump ;;
    restore) cmd_restore "${1:-$DUMP_LOCAL}" ;;
    cutover) cmd_cutover "$@" ;;
    wipe) cmd_wipe_and_recreate ;;
    -h|--help|help|"") usage; exit 0 ;;
    *) die "Unknown command: ${cmd}" ;;
  esac
}

main "$@"
