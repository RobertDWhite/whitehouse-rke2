#!/usr/bin/env bash
set -euo pipefail

LH_NS="longhorn-system"
ARGO_NS="argocd"
SC="longhorn"

echo "🚨 FULL LONGHORN DISASTER RECOVERY STARTING"
echo "=========================================="

############################################
# 1️⃣ STOP ARGOCD HARD
############################################
echo "🛑 Stopping ArgoCD controllers"
kubectl -n "$ARGO_NS" scale deploy argocd-application-controller --replicas=0 || true
kubectl -n "$ARGO_NS" scale deploy argocd-applicationset-controller --replicas=0 || true

############################################
# 2️⃣ DELETE ALL PVCs (FORCE + FINALIZERS)
############################################
echo "💣 Deleting ALL PVCs (force, removing finalizers)"

kubectl get pvc -A -o json \
| jq -r '
.items[]
| select(.metadata.namespace | test("^(kube-system|longhorn-system|argocd)$") | not)
| "\(.metadata.namespace) \(.metadata.name)"
' | while read -r ns pvc; do
  echo "❌ $ns/$pvc"
  kubectl -n "$ns" patch pvc "$pvc" -p '{"metadata":{"finalizers":null}}' --type=merge || true
  kubectl -n "$ns" delete pvc "$pvc" --force --grace-period=0 || true
done

echo "⏳ Waiting for ALL PVCs to disappear..."
until [ "$(kubectl get pvc -A --no-headers 2>/dev/null | wc -l)" -eq 0 ]; do
  sleep 3
done
echo "✅ PVC layer cleared"

############################################
# 3️⃣ RESTORE LONGHORN VOLUMES FROM BACKUPS
############################################
echo "🔄 Restoring Longhorn volumes from backups"

kubectl -n "$LH_NS" get backups.longhorn.io -o json \
| jq -r '
.items[]
| select(.status != null)
| select((.status | type) == "object")
| select(.status.state == "Completed")
| select(.status.volumeName != null)
| select(.status.volumeSize != null)
| select(.status.url != null)
| [
    .status.volumeName,
    .status.volumeSize,
    .status.url
  ]
| @tsv
' | sort -u \
| while IFS=$'\t' read -r VOL SIZE URL; do

  if kubectl -n "$LH_NS" get volumes.longhorn.io "$VOL" &>/dev/null; then
    echo "↩️  Volume $VOL already exists, skipping"
    continue
  fi

  echo "📦 Restoring volume $VOL"
  kubectl -n "$LH_NS" apply -f - <<EOF
apiVersion: longhorn.io/v1beta2
kind: Volume
metadata:
  name: $VOL
spec:
  fromBackup: "$URL"
  numberOfReplicas: 2
  size: "$SIZE"
  frontend: blockdev
EOF
done

############################################
# 4️⃣ RECREATE PVCs IN ORIGINAL NAMESPACES
############################################
echo "📎 Recreating PVCs from backup metadata"

kubectl -n "$LH_NS" get backups.longhorn.io -o json \
| jq -r '
.items[]
| select(.status != null)
| select((.status | type) == "object")
| select(.status.state == "Completed")
| select(.status.labels != null)
| select(.status.labels.KubernetesStatus != null)
| select((.status.labels.KubernetesStatus | type) == "string")
| {
    meta: (.status.labels.KubernetesStatus | fromjson),
    volume: .status.volumeName,
    size: .status.volumeSize
  }
| select(.meta.pvcName != null)
| select(.meta.namespace != null)
| [
    .meta.namespace,
    .meta.pvcName,
    .volume,
    .size
  ]
| @tsv
' | sort -u \
| while IFS=$'\t' read -r NS PVC VOL SIZE; do

  kubectl get ns "$NS" >/dev/null 2>&1 || kubectl create ns "$NS"

  echo "🔗 PVC $NS/$PVC → $VOL"

  kubectl delete pvc -n "$NS" "$PVC" --ignore-not-found --force --grace-period=0 || true
  while kubectl get pvc -n "$NS" "$PVC" >/dev/null 2>&1; do sleep 2; done

  kubectl apply -f - <<EOF
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: $PVC
  namespace: $NS
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: $SC
  volumeName: $VOL
  resources:
    requests:
      storage: "$SIZE"
EOF
done

############################################
# 5️⃣ WAIT FOR PVCs TO BIND
############################################
echo "⏳ Waiting for PVCs to bind"
kubectl wait pvc --all -A --for=condition=Bound --timeout=30m || true

############################################
# 6️⃣ RESTART ALL WORKLOADS
############################################
echo "🔁 Restarting workloads"

kubectl get ns -o json \
| jq -r '.items[].metadata.name' \
| grep -Ev '^(kube-system|longhorn-system|argocd)$' \
| while read -r ns; do
  kubectl rollout restart deploy -n "$ns" || true
  kubectl rollout restart statefulset -n "$ns" || true
done

############################################
# 7️⃣ BRING ARGOCD BACK
############################################
echo "🚀 Restarting ArgoCD"
kubectl -n "$ARGO_NS" scale deploy argocd-application-controller --replicas=1
kubectl -n "$ARGO_NS" scale deploy argocd-applicationset-controller --replicas=1

echo "🎉 RECOVERY COMPLETE"
echo "Volumes restored → PVCs rebound → workloads restarted"