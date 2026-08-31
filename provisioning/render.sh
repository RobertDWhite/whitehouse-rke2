#!/usr/bin/env bash
# Render cloud-init for one node: ./render.sh rke2-node-13 [--iso|--autoinstall]
#   --iso          build build/<node>/seed.iso (NoCloud) — for the cloud-image OVA
#   --autoinstall  build build/<node>/autoinstall-seed.iso — for the normal
#                  ubuntu live-server installer ISO (unattended install, WIPES DISK)
# Requires: sops (with the age private key available), envsubst.
set -euo pipefail
cd "$(dirname "$0")"

NODE="${1:?usage: ./render.sh <node-name> [--iso]}"
MAKE_ISO="${2:-}"

NODE_FILE="nodes/${NODE}.env"
[[ -f "$NODE_FILE" ]] || { echo "ERROR: $NODE_FILE not found (copy nodes/rke2-node-XX.env.example)"; exit 1; }

set -a
# shellcheck disable=SC1091
source nodes/_defaults.env
# shellcheck disable=SC1090
source "$NODE_FILE"
# Secrets (RKE2_TOKEN, ...) — decrypted in-memory only, never written to disk
eval "$(sops -d cloud-init/secrets.sops.env)"
set +a

# New instance-id each render so cloud-init re-runs on a rebuilt VM
INSTANCE_SERIAL="$(date +%Y%m%d%H%M%S)"
export INSTANCE_SERIAL

for v in NODE_HOSTNAME NODE_IP NODE_GATEWAY RKE2_TYPE RKE2_TOKEN SSH_PUBKEY ADMIN_USER ADMIN_GECOS; do
  val="${!v:-}"
  if [[ -z "$val" || "$val" == *CHANGEME* ]]; then
    echo "ERROR: $v is unset or still CHANGEME"; exit 1
  fi
done

VARS='${NODE_HOSTNAME} ${NODE_IP} ${NODE_PREFIX} ${NODE_GATEWAY} ${NODE_IFACE} ${RKE2_TYPE} ${RKE2_TOKEN} ${RKE2_SERVER_URL} ${SSH_PUBKEY} ${ADMIN_USER} ${ADMIN_GECOS} ${INSTANCE_SERIAL}'
OUT="build/${NODE}"
mkdir -p "$OUT"
envsubst "$VARS" < cloud-init/user-data.yaml.tmpl      > "$OUT/user-data"
envsubst "$VARS" < cloud-init/meta-data.yaml.tmpl      > "$OUT/meta-data"
envsubst "$VARS" < cloud-init/network-config.yaml.tmpl > "$OUT/network-config"
chmod 600 "$OUT/user-data"   # contains the rke2 token
echo "rendered -> $OUT/{user-data,meta-data,network-config}"

make_cidata_iso() {  # $1 = source dir, $2 = output iso
  rm -f "$2"
  if command -v hdiutil >/dev/null; then          # macOS
    hdiutil makehybrid -iso -joliet -default-volume-name cidata -o "$2" "$1" >/dev/null
  elif command -v genisoimage >/dev/null; then    # linux
    genisoimage -output "$2" -volid cidata -joliet -rock "$1"/* >/dev/null
  else
    echo "ERROR: need hdiutil or genisoimage to build an ISO"; exit 1
  fi
}

case "$MAKE_ISO" in
  --iso)
    make_cidata_iso "$OUT" "$OUT/seed.iso"
    echo "ISO -> $OUT/seed.iso  (attach to a cloud-image OVA VM; label=cidata)"
    ;;
  --autoinstall)
    # Wrap the rendered config in an autoinstall document for the live-server
    # installer ISO: subiquity installs unattended, then the installed system
    # runs our user-data on its first boot.
    AI="$OUT/autoinstall"
    mkdir -p "$AI"
    {
      echo "#cloud-config"
      echo "autoinstall:"
      echo "  version: 1"
      echo "  locale: en_US.UTF-8"
      echo "  keyboard:"
      echo "    layout: us"
      echo "  storage:"
      echo "    layout:"
      echo "      name: direct"
      echo "  network:"
      sed 's/^/    /' "$OUT/network-config"
      echo "  ssh:"
      echo "    install-server: true"
      echo "    allow-pw: false"
      echo "  user-data:"
      tail -n +2 "$OUT/user-data" | sed 's/^/    /'
      echo "  shutdown: reboot"
    } > "$AI/user-data"
    cp "$OUT/meta-data" "$AI/meta-data"
    chmod 600 "$AI/user-data"
    make_cidata_iso "$AI" "$OUT/autoinstall-seed.iso"
    echo "ISO -> $OUT/autoinstall-seed.iso"
    echo "Attach BOTH the ubuntu live-server ISO and this one as CD-ROMs, boot"
    echo "from the installer. It will ask ONE 'continue with autoinstall?'"
    echo "confirmation, then wipe the disk, install, reboot, and join the cluster."
    ;;
esac
