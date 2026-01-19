#!/usr/bin/env bash
set -euo pipefail

LH_NS=longhorn-system
SC=longhorn

echo "🔁 Restoring PVCs from Longhorn backups (clean rebuild)..."

kubectl -n "$LH_NS" get backups.longhorn.io -o json \
| jq -r '
.items[]
| select(.status.state=="Completed")
| {
    pvc: (.status.labels.KubernetesStatus | fromjson | .pvcName),
    ns:  (.status.labels.KubernetesStatus | fromjson | .namespace),
    size: .status.volumeSize,
    volume: .status.volumeName
  }
| select(.pvc != null and .ns != null and .volume != null)
| @base64
' | while read row; do
  obj=$(echo "$row" | base64 --decode)

  pvc=$(echo "$obj" | jq -r .pvc)
  ns=$(echo "$obj" | jq -r .ns)
  size=$(echo "$obj" | jq -r .size)
  volume=$(echo "$obj" | jq -r .volume)

  echo "➡️  Restoring PVC $ns/$pvc → $volume"

  kubectl get ns "$ns" >/dev/null 2>&1 || kubectl create ns "$ns"

  cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: $pvc
  namespace: $ns
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: $SC
  volumeName: $volume
  resources:
    requests:
      storage: $size
EOF
done

echo "✅ PVC restore complete"