#!/bin/sh
# Trims Velero backups on the NAS down to n and n-1 per schedule (see
# select.jq for exactly what is kept). Deletes go through DeleteBackupRequest,
# which is what `velero backup delete` does - deleting the Backup CR directly
# leaves the data in the bucket and the backup sync controller re-creates it.
#
# Freed space shows up only after kopia repository maintenance has run, since
# kopia blobs are content-addressed and shared between snapshots.
set -eu

NS=velero
KEEP="${KEEP:-2}"
DRY_RUN="${DRY_RUN:-false}"
DIR="$(dirname "$0")"
TMP="$(mktemp -d)"

kubectl -n "$NS" get backups.velero.io -o json > "$TMP/backups.json"
kubectl -n "$NS" get podvolumebackups.velero.io -o json > "$TMP/pvbs.json"
kubectl -n "$NS" get backupstoragelocations.velero.io -o json > "$TMP/bsls.json"

if [ "$(jq '.items | length' "$TMP/backups.json")" -eq 0 ]; then
  echo "no backups listed - refusing to do anything"
  exit 1
fi

jq -r --argjson keep "$KEEP" \
  --slurpfile pvbs "$TMP/pvbs.json" \
  --slurpfile bsls "$TMP/bsls.json" \
  -f "$DIR/select.jq" "$TMP/backups.json" > "$TMP/delete.tsv"

echo "$(wc -l < "$TMP/delete.tsv" | tr -d ' ') backup(s) to delete (keep=$KEEP, dry_run=$DRY_RUN)"

while IFS="$(printf '\t')" read -r name uid; do
  [ -n "$name" ] || continue
  if [ "$DRY_RUN" = "true" ]; then
    echo "would delete $name"
    continue
  fi
  if kubectl -n "$NS" get deletebackuprequests.velero.io -l "velero.io/backup-name=$name" -o name | grep -q .; then
    echo "already requested $name"
    continue
  fi
  echo "deleting $name"
  kubectl create -f - <<EOF
apiVersion: velero.io/v1
kind: DeleteBackupRequest
metadata:
  generateName: ${name}-
  namespace: ${NS}
  labels:
    velero.io/backup-name: ${name}
    velero.io/backup-uid: ${uid}
spec:
  backupName: ${name}
EOF
done < "$TMP/delete.tsv"
