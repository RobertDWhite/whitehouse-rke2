#!/bin/bash
# Install + configure the NUT server on the node with the UPS attached (rke2-node-12).
# Usage: run as root on the node.
#   ADMIN_PW=... UPSMON_REMOTE_PW... MONUSER_PW=secret ./setup-server.sh
# If passwords are unset, random ones are generated for admin/upsmon_remote and
# monuser defaults to "secret" (the value Synology DSM uses for a network UPS server).
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
HERE="$(cd "$(dirname "$0")/../server" && pwd)"

ADMIN_PW="${ADMIN_PW:-$(openssl rand -hex 12)}"
UPSMON_REMOTE_PW="${UPSMON_REMOTE_PW:-$(openssl rand -hex 12)}"
MONUSER_PW="${MONUSER_PW:-secret}"

apt-get update -qq
apt-get install -y -qq nut

install -m640 -o root -g nut "$HERE/nut.conf"        /etc/nut/nut.conf
install -m640 -o root -g nut "$HERE/ups.conf"        /etc/nut/ups.conf
install -m640 -o root -g nut "$HERE/upsd.conf"       /etc/nut/upsd.conf
install -m755          "$HERE/upssched-cmd"          /etc/nut/upssched-cmd
install -m640 -o root -g nut "$HERE/upssched.conf"   /etc/nut/upssched.conf

sed -e "s/__ADMIN_PW__/$ADMIN_PW/" -e "s/__UPSMON_REMOTE_PW__/$UPSMON_REMOTE_PW/" -e "s/__MONUSER_PW__/$MONUSER_PW/" \
    "$HERE/upsd.users.example" > /etc/nut/upsd.users
sed -e "s/__ADMIN_PW__/$ADMIN_PW/" "$HERE/upsmon.conf.example" > /etc/nut/upsmon.conf
chown root:nut /etc/nut/upsd.users /etc/nut/upsmon.conf
chmod 640      /etc/nut/upsd.users /etc/nut/upsmon.conf

systemctl enable nut-driver.target nut-driver-enumerator.path nut-server nut-monitor
systemctl restart nut-driver.target nut-server nut-monitor
sleep 3
upsc ups 2>/dev/null | grep -E 'ups.status|battery.charge:|battery.runtime:|ups.model' || true

echo
echo "Save these to 1Password (NOT to git):"
echo "  admin         = $ADMIN_PW"
echo "  upsmon_remote = $UPSMON_REMOTE_PW"
echo "  monuser       = $MONUSER_PW"
