#!/usr/bin/env bash
# Deploy a node VM to ESXi from the Ubuntu cloud-image OVA: renders the seed,
# bakes an OVA pre-sized to DISK_GB (see make-ova.sh), imports it thin,
# attaches the seed ISO, optionally boots.
#   usage: ./deploy.sh rke2-node-16 [--power-on]
# Requires: govc, op (1Password CLI), and render.sh's own requirements.
# Host/creds: set ESXI_URL in nodes/_defaults.env; credentials come from
# 1Password at runtime (never stored here). Override with GOVC_USERNAME /
# GOVC_PASSWORD env vars if op is unavailable.
set -euo pipefail
cd "$(dirname "$0")"

NODE="${1:?usage: ./deploy.sh <node-name> [--power-on]}"
POWER_ON="${2:-}"

set -a
# shellcheck disable=SC1091
source nodes/_defaults.env
# shellcheck disable=SC1090
source "nodes/${NODE}.env"
set +a

# Each VM node lives on its own standalone ESXi host: rke2-node-NN -> 10.100.1.NN
if [[ -z "${ESXI_URL:-}" || "$ESXI_URL" == *CHANGEME* ]]; then
  ESXI_URL="10.100.1.${NODE_HOSTNAME##*-}"
fi
: "${OVA_PATH:?set OVA_PATH (local path to ubuntu-24.04-server-cloudimg-amd64.ova)}"
[[ -f "$OVA_PATH" ]] || { echo "ERROR: OVA not found at $OVA_PATH"; exit 1; }

export GOVC_URL="$ESXI_URL"
export GOVC_INSECURE=1
if [[ -z "${GOVC_PASSWORD:-}" ]]; then
  export OP_ACCOUNT="${OP_ACCOUNT:-my.1password.com}"
  export GOVC_USERNAME="$(op item get "$ESXI_OP_ITEM" --fields username 2>/dev/null)"
  export GOVC_PASSWORD="$(op item get "$ESXI_OP_ITEM" --fields password --reveal 2>/dev/null)"
fi
[[ -n "$GOVC_PASSWORD" ]] || { echo "ERROR: no ESXi credentials (op signed out?)"; exit 1; }

govc about >/dev/null || { echo "ERROR: cannot reach $ESXI_URL"; exit 1; }

./render.sh "$NODE" --iso

if govc vm.info "$NODE" 2>/dev/null | grep -q "Name:"; then
  echo "ERROR: VM '$NODE' already exists — destroy it first:"
  echo "  govc vm.destroy '$NODE'"
  exit 1
fi

# Bake the disk size into the OVA descriptor instead of growing the vmdk
# after import — vm.disk.change on the ESXi side was painfully slow. The VM
# is born with a ${DISK_GB}G thin disk; cloud-init growpart fills it at boot.
OVA_SIZED="$(./make-ova.sh "$DISK_GB" "$OVA_PATH")"

echo "==> importing OVA as $NODE (ds=$VM_DATASTORE net=$VM_NETWORK disk=${DISK_GB}G thin)"
SPEC="build/${NODE}/import-spec.json"
govc import.spec "$OVA_SIZED" | python3 -c \
  'import json,sys; s=json.load(sys.stdin); s["DiskProvisioning"]="thin"; json.dump(s,sys.stdout)' \
  > "$SPEC"
govc import.ova -options "$SPEC" -name "$NODE" -ds "$VM_DATASTORE" -net "$VM_NETWORK" "$OVA_SIZED"

echo "==> sizing: ${VM_CPU} vCPU, ${VM_MEM_MB} MB RAM"
govc vm.change -vm "$NODE" -c "$VM_CPU" -m "$VM_MEM_MB"

echo "==> attaching seed ISO"
govc datastore.upload -ds "$VM_DATASTORE" \
  "build/${NODE}/seed.iso" "isos/${NODE}-seed.iso"
CDROM=$(govc device.cdrom.add -vm "$NODE")
govc device.cdrom.insert -vm "$NODE" -device "$CDROM" -ds "$VM_DATASTORE" "isos/${NODE}-seed.iso"
govc device.connect -vm "$NODE" "$CDROM"

if [[ "$POWER_ON" == "--power-on" ]]; then
  govc vm.power -on "$NODE"
  echo "==> powered on. Watch: kubectl get nodes -w   then: ssh <admin-user>@${NODE_IP}"
else
  echo "==> ready. Review in ESXi, then: govc vm.power -on '$NODE'"
fi
