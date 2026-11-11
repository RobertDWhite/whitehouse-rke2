#!/usr/bin/env bash
# Run as root on an Ubuntu RKE2 host: configure-host.sh rke2-node-N
set -euo pipefail
node=${1:?canonical Kubernetes node name required}
[[ "$node" =~ ^rke2-node-[0-9]+$ ]] || exit 2
command -v rsyslogd >/dev/null
install -d -m 0755 /var/spool/rsyslog /etc/rsyslog.d
config=/etc/rsyslog.d/60-security-forward.conf
if [[ -f "$config" ]]; then cp -p "$config" "${config}.previous"; fi
cat > "$config" <<EOF
# Managed by whitehouse-rke2/observability/observability/security/configure-host.sh
# LAN-only TCP RFC5424; disk-assisted queue retains logs across collector outages.
global(localHostname="$node")
*.* action(type="omfwd" target="10.99.5.111" port="1514" protocol="tcp"
  template="RSYSLOG_SyslogProtocol23Format" TCP_Framing="octet-counted"
  action.resumeRetryCount="-1" queue.type="LinkedList"
  queue.filename="security-forward" queue.spoolDirectory="/var/spool/rsyslog"
  queue.maxDiskSpace="256m" queue.saveOnShutdown="on")
EOF
if ! rsyslogd -N1; then
 if [[ -f "${config}.previous" ]]; then mv "${config}.previous" "$config"; else rm "$config"; fi
 exit 1
fi
cat > /etc/cron.d/security-log-heartbeat <<EOF
* * * * * root /usr/bin/logger -t siem-heartbeat 'SIEM_HEARTBEAT host=$node'
EOF
chmod 0644 /etc/cron.d/security-log-heartbeat
systemctl restart rsyslog
systemctl is-active --quiet rsyslog
logger -t siem-heartbeat "SIEM_HEARTBEAT host=$node setup=complete"
echo "Configured syslog and heartbeat: $node"
