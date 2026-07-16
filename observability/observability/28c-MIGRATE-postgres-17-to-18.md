# Grafana Postgres: 17 → 18 major upgrade

**Do not** merge a bare image-tag bump (`17-alpine` → `18-alpine`) against an
existing PVC. Postgres major versions are not on-disk compatible, and the
official Docker image also changed its volume/`PGDATA` layout in 18.

This directory is updated to the **Postgres 18 end state**. Cut over with the
script (or the manual steps below). Supersedes Renovate PR #565.

## What changed in manifests

| Item | Postgres ≤17 (old) | Postgres 18 (new) |
|------|--------------------|-------------------|
| Image tag | `17-alpine` | `18-alpine` |
| Volume mount | `/var/lib/postgresql/data` | `/var/lib/postgresql` |
| `PGDATA` | `/var/lib/postgresql/data/pgdata` (explicit) | default `/var/lib/postgresql/18/docker` |
| PVC name | `data-grafana-postgres-0` | same name (must be **deleted** and recreated empty) |

Consumer is only Grafana (`GF_DATABASE_HOST=grafana-postgres:5432`). Dashboards
and datasources are file-provisioned; users, SA tokens, annotations, and alert
history live in this DB (~14 MB today) and **are preserved by dump/restore**.

## Recommended: one script

From a machine with `kubectl` context for this cluster and repo checkout:

```bash
# Dry-run: dump only, print next steps
./observability/observability/scripts/migrate-grafana-postgres-17-to-18.sh dump

# Full cutover (dump → wipe PVC → wait for PG18 → restore)
# Run AFTER this PR is merged/synced (or pass --apply-local to kubectl-apply
# the updated StatefulSet from your working tree before Argo catches up).
./observability/observability/scripts/migrate-grafana-postgres-17-to-18.sh cutover
```

Expected downtime: a few minutes while Grafana cannot reach Postgres.

## Manual steps (equivalent)

### 1. Dump while still on 17

```bash
kubectl -n observability exec grafana-postgres-0 -- \
  pg_dump -U grafana -d grafana -Fc -f /tmp/grafana.dump
kubectl -n observability cp \
  grafana-postgres-0:/tmp/grafana.dump ./grafana-postgres-17.dump
```

### 2. Merge / sync this PR

ArgoCD (or `kubectl apply -k observability/observability`) rolls the StatefulSet
to `postgres:18-alpine` with the new mount path. **If the old PVC is still
attached**, the pod will CrashLoop (`database files are incompatible` or empty
`PGDATA` path). That is expected until step 3.

### 3. Recreate the data volume empty

`volumeClaimTemplates` are immutable for an existing STS identity; we keep the
claim name `data` and force a fresh volume:

```bash
kubectl -n observability delete pod grafana-postgres-0 --wait=false
kubectl -n observability delete pvc data-grafana-postgres-0 --wait=true
# StatefulSet recreates the pod + PVC; PG18 initializes a new cluster.
kubectl -n observability rollout status statefulset/grafana-postgres --timeout=5m
kubectl -n observability exec grafana-postgres-0 -- \
  psql -U grafana -d grafana -c 'SHOW server_version;'
# Expect 18.x
```

### 4. Restore

```bash
kubectl -n observability cp ./grafana-postgres-17.dump \
  grafana-postgres-0:/tmp/grafana.dump
kubectl -n observability exec grafana-postgres-0 -- \
  pg_restore -U grafana -d grafana --clean --if-exists --no-owner \
  /tmp/grafana.dump
kubectl -n observability rollout restart deployment/grafana
```

### 5. Verify

```bash
kubectl -n observability exec grafana-postgres-0 -- \
  psql -U grafana -d grafana -c \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';"
# Hit https://grafana.white.fm — login / dashboards / SA tokens as before.
```

## Rollback

1. Keep `grafana-postgres-17.dump` until verified.
2. Revert this PR (image `17-alpine` + old mount/`PGDATA`).
3. Delete `data-grafana-postgres-0` again, let STS recreate, restore the dump
   with `postgres:17` (`pg_restore` from the same custom-format dump works on 17).

If you still have the **pre-cutover** Longhorn volume snapshot/backup of
`data-grafana-postgres-0`, you can reattach that volume only with the **17**
image and the **old** mount/`PGDATA` — not with 18.

## Out of scope

Other Postgres instances in this repo (Authentik, Matrix, listmonk, etc.) use
their own image pins and are not upgraded by this change.
