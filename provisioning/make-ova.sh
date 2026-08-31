#!/usr/bin/env bash
# Bake an Ubuntu cloud-image OVA whose disk is declared at the target size,
# so the VM is created with the right disk from the start — no slow
# post-import grow in ESXi. Only the OVF capacity is patched; the vmdk
# content is untouched, so the output stays ~the same size as the source.
#   usage: ./make-ova.sh <size-gb> [source-ova]
# Prints the path of the baked OVA on stdout (progress goes to stderr).
# Results are cached in build/ova/ per size; re-bakes if the source is newer.
set -euo pipefail
cd "$(dirname "$0")"

SIZE_GB="${1:?usage: ./make-ova.sh <size-gb> [source-ova]}"
SRC="${2:-${OVA_PATH:-}}"
: "${SRC:?set OVA_PATH or pass the source OVA as the second argument}"
[[ -f "$SRC" ]] || { echo "ERROR: OVA not found at $SRC" >&2; exit 1; }
[[ "$SIZE_GB" =~ ^[0-9]+$ ]] || { echo "ERROR: size must be an integer (GB)" >&2; exit 1; }

OUT="build/ova/$(basename "${SRC%.ova}")-${SIZE_GB}G.ova"
if [[ -s "$OUT" && "$OUT" -nt "$SRC" ]]; then
  echo "==> using cached $OUT" >&2
  echo "$OUT"
  exit 0
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
tar -xf "$SRC" -C "$WORK"

OVF="$(ls "$WORK"/*.ovf)"
MF="$(ls "$WORK"/*.mf)"
VMDK="$(ls "$WORK"/*.vmdk)"
BYTES=$(( SIZE_GB * 1024 * 1024 * 1024 ))

# Ubuntu's OVF declares capacity in plain bytes; refuse anything else rather
# than silently mis-sizing the disk.
grep -q 'ovf:capacityAllocationUnits="byte"' "$OVF" \
  || { echo "ERROR: unexpected capacity units in $(basename "$OVF")" >&2; exit 1; }
sed -i.bak -E "s/ovf:capacity=\"[0-9]+\"/ovf:capacity=\"$BYTES\"/" "$OVF"
rm -f "$OVF.bak"
grep -q "ovf:capacity=\"$BYTES\"" "$OVF" \
  || { echo "ERROR: capacity patch failed" >&2; exit 1; }

# The manifest pins the OVF by hash — recompute it or import will reject.
OVF_SUM="$(shasum -a 256 "$OVF" | awk '{print $1}')"
sed -i.bak -E "s/^SHA256\($(basename "$OVF")\)= .*/SHA256($(basename "$OVF"))= $OVF_SUM/" "$MF"
rm -f "$MF.bak"

# OVF descriptor must be the first member of the tar (OVF spec requirement).
mkdir -p "$(dirname "$OUT")"
tar -cf "$OUT" -C "$WORK" \
  "$(basename "$OVF")" "$(basename "$MF")" "$(basename "$VMDK")"

echo "==> baked $OUT (${SIZE_GB}G disk)" >&2
echo "$OUT"
