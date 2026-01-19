#!/usr/bin/env bash
set -euo pipefail

echo "🔒 ArgoCD assumed stopped — rebinding PVCs to restored Longhorn volumes"

LH_NS="longhorn-system"
SC="longhorn"

kubectl -n "$LH_NS" get backups.longhorn.io -o json \
| jq -r '
.items[]
| select(.status.state=="Completed")
| select(.status.labels.KubernetesStatus != null)
| {
    pvc: (.status.labels.KubernetesStatus | fromjson | .pvcName),
    ns:  (.status.labels.KubernetesStatus | fromjson | .namespace),
    size: .status.volumeSize,
    volume: .status.volumeName
  }
| select(.pvc != null and .ns != null and .volume != null)
| @base64
' | while read -r row; do
  obj=$(echo "$row" | base64 --decode)
  pvc=$(echo "$obj" | jq -r .pvc)
  ns=$(echo "$obj" | jq -r .ns)
  size=$(echo "$obj" | jq -r .size)
  volume=$(echo "$obj" | jq -r .volume)

  echo "🔗 Ensuring PVC $ns/$pvc → $volume"

  # Ensure namespace exists
  kubectl get ns "$ns" >/dev/null 2>&1 || kubectl create ns "$ns"

  if kubectl -n "$ns" get pvc "$pvc" >/dev/null 2>&1; then
    echo "   🔁 PVC exists — patching volumeName"
    kubectl -n "$ns" patch pvc "$pvc" --type=merge -p "{
      \"spec\": {
        \"volumeName\": \"$volume\"
      }
    }"
  else
    echo "   🆕 PVC missing — creating bound PVC"
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
  fi
done

echo "✅ PVC rebinding complete"