#!/bin/sh
# Run restic against the Obsidian backup repo from any machine with kubectl.
# The repo sits on the Synology NFS export, which only admits cluster nodes, so
# restic runs in a throwaway pod (repo mounted read-only, --no-lock) via
# kubectl exec, so stdout (including tar archives) streams back here intact.
#
#   ./restic.sh snapshots
#   ./restic.sh ls latest /export
#   ./restic.sh dump --archive tar latest /data   > couchdb-data.tar
#   ./restic.sh dump --archive tar latest /export > couchdb-export.tar
#   ./restic.sh dump --archive tar 2409c4ac /     > snapshot.tar
#   RW=1 ./restic.sh tag --add partial --remove hourly <id>   (read-write)
set -eu
if [ "${RW:-0}" = 1 ]; then ro=false; lock=; else ro=true; lock=--no-lock; fi
[ $# -gt 0 ] || { sed -n '2,12p' "$0"; exit 1; }

pod="restic-cli-$(date +%s)"
overrides=$(cat <<EOF
{"spec":{
  "automountServiceAccountToken":false,
  "affinity":{"nodeAffinity":{"requiredDuringSchedulingIgnoredDuringExecution":{"nodeSelectorTerms":[
    {"matchExpressions":[{"key":"kubernetes.io/hostname","operator":"NotIn","values":["rke2-node-10","rke2-node-50"]}]}]}}},
  "securityContext":{"runAsNonRoot":true,"runAsUser":5984,"runAsGroup":5984,"seccompProfile":{"type":"RuntimeDefault"}},
  "containers":[{
    "name":"restic","image":"restic/restic:0.19.1@sha256:136600b6ff6843d61d355f7f71f460a166429f35de6fd11b568fece3c9a4d510",
    "command":["sleep","3600"],
    "env":[{"name":"RESTIC_REPOSITORY","value":"/repo"},{"name":"RESTIC_CACHE_DIR","value":"/tmp/cache"},
           {"name":"RESTIC_PASSWORD","valueFrom":{"secretKeyRef":{"name":"restic","key":"RESTIC_PASSWORD"}}}],
    "securityContext":{"allowPrivilegeEscalation":false,"capabilities":{"drop":["ALL"]}},
    "resources":{"requests":{"cpu":"100m","memory":"128Mi"},"limits":{"memory":"1Gi"}},
    "volumeMounts":[{"name":"nfs-root","mountPath":"/repo","subPath":"obsidian-restic","readOnly":$ro},
                    {"name":"tmp","mountPath":"/tmp"}]}],
  "volumes":[{"name":"nfs-root","nfs":{"server":"10.100.1.20","path":"/volume1/longhorn-backups"}},
             {"name":"tmp","emptyDir":{}}]}}
EOF
)
trap 'kubectl delete pod -n obsidian "$pod" --wait=false >/dev/null 2>&1' EXIT
kubectl run "$pod" -n obsidian --restart=Never --image=restic/restic:0.19.1 --overrides="$overrides" >/dev/null
kubectl wait -n obsidian "pod/$pod" --for=condition=Ready --timeout=180s >/dev/null
kubectl exec -n obsidian "$pod" -- restic $lock "$@"
