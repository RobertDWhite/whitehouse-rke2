#!/bin/bash
# Install + configure a NUT secondary (upsmon only) on a non-primary RKE2 node.
# Usage: run as root on the node.
#   UPSMON_REMOTE_PW=... ./setup-client.sh
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
HERE="$(cd "$(dirname "$0")/../client" && pwd)"
: "${UPSMON_REMOTE_PW:?set UPSMON_REMOTE_PW (from the server's upsd.users)}"

dpkg -l nut-client >/dev/null 2>&1 || { apt-get update -qq; apt-get install -y -qq nut-client; }

install -m640 -o root -g nut "$HERE/nut.conf" /etc/nut/nut.conf
sed -e "s/__UPSMON_REMOTE_PW__/$UPSMON_REMOTE_PW/" "$HERE/upsmon.conf.example" > /etc/nut/upsmon.conf
chown root:nut /etc/nut/upsmon.conf
chmod 640      /etc/nut/upsmon.conf

systemctl enable nut-monitor
systemctl restart nut-monitor
sleep 3
systemctl is-active nut-monitor
upsc ups@10.99.5.12 ups.status 2>/dev/null || true
