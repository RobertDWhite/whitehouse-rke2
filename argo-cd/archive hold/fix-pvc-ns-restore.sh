#!/usr/bin/env bash
set -euo pipefail

LH_NS=longhorn-system
SC=longhorn

echo "🔍 Fixing PVC namespaces using Longhorn backup metadata..."

kubectl -n $LH_NS get backups.longhorn.io -o json |
jq -r '
  .items
  | map(
      select(
        .status.volumeName != null and
        .status.labels != null and
        .status.labels.KubernetesStatus != null and
        (.status.labels.KubernetesStatus | type == "string")
      )
    )
  | sort_by(.status.backupCreatedAt)
  | group_by(.status.volumeName)
  | .[]
  | last
  | (
      .status.labels.KubernetesStatus
      | fromjson
      | [
          .namespace,
          .pvcName
        ]
    )
    + [
        .status.volumeName
      ]
  | @tsv
' | while IFS=$'\t' read -r TARGET_NS TARGET_PVC_NAME PVC_NAME; do

  if [[ -z "$TARGET_NS" || "$TARGET_NS" == "null" ]]; then
    echo "⚠️  Skipping $PVC_NAME (no namespace metadata)"
    continue
  fi

  # Already correct
  if kubectl -n "$TARGET_NS" get pvc "$PVC_NAME" &>/dev/null; then
    echo "✅ PVC $PVC_NAME already exists in $TARGET_NS, skipping"
    continue
  fi

  # Only move from default
  if ! kubectl -n default get pvc "$PVC_NAME" &>/dev/null; then
    echo "⚠️  PVC $PVC_NAME not found in default, skipping"
    continue
  fi

  echo "➡️  Moving PVC $PVC_NAME → namespace $TARGET_NS"

  SIZE=$(kubectl -n default get pvc "$PVC_NAME" -o jsonpath='{.spec.resources.requests.storage}')

  # Delete PVC in default (volume remains intact)
  kubectl delete pvc "$PVC_NAME" -n default

  # Recreate PVC in correct namespace
  kubectl apply -f - <<EOF
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: $PVC_NAME
  namespace: $TARGET_NS
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: $SC
  volumeName: $PVC_NAME
  resources:
    requests:
      storage: "$SIZE"
EOF

  echo "   ✅ PVC recreated in $TARGET_NS"

done

echo "🎉 PVC namespace reconciliation complete."