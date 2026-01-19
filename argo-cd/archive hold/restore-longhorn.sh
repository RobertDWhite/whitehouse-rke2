#!/usr/bin/env bash
set -euo pipefail

LH_NS=longhorn-system
SC=longhorn

echo "🔍 Discovering Longhorn backups (latest per PVC)..."

kubectl -n $LH_NS get backups.longhorn.io -o json |
jq -r '
  .items
  | sort_by(.status.backupCreatedAt)
  | group_by(.status.volumeName)
  | .[]
  | last
  | [
      .metadata.name,
      .status.volumeName,
      .status.volumeSize,
      .status.url
    ]
  | @tsv
' | while IFS=$'\t' read -r BACKUP_NAME PVC_NAME SIZE BACKUP_URL; do

  if [[ -z "$PVC_NAME" || -z "$BACKUP_URL" ]]; then
    echo "⚠️  Skipping invalid backup (missing PVC or URL)"
    continue
  fi

  echo "➡️  Restoring PVC: $PVC_NAME"
  echo "    Backup: $BACKUP_NAME"
  echo "    Size: $SIZE"

  if kubectl -n $LH_NS get volumes.longhorn.io "$PVC_NAME" &>/dev/null; then
    echo "    ✅ Volume already exists, skipping"
    continue
  fi

  # Restore Longhorn volume
  kubectl -n $LH_NS apply -f - <<EOF
apiVersion: longhorn.io/v1beta2
kind: Volume
metadata:
  name: $PVC_NAME
spec:
  fromBackup: "$BACKUP_URL"
  numberOfReplicas: 2
  size: "$SIZE"
  frontend: blockdev
EOF

  # Create PVC (namespace can be corrected later by apps)
  kubectl apply -f - <<EOF
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: $PVC_NAME
  namespace: default
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: $SC
  volumeName: $PVC_NAME
  resources:
    requests:
      storage: "$SIZE"
EOF

done

echo "🎉 Restore complete. Volumes recreated from backups."