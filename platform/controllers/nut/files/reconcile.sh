#!/bin/sh
# Runs inside the privileged nut-installer DaemonSet pod.
# Idempotently installs + configures host NUT via nsenter into PID 1, then loops
# to re-assert config (self-heal drift). Runtime NUT stays on host systemd so the
# UPS shutdown path does not depend on the cluster being healthy.
#
# ROLE          = server | client   (set per DaemonSet)
# passwords     = ADMIN_PW / UPSMON_REMOTE_PW / MONUSER_PW  (env, from Secret)
# config bodies = /nut-config/{server,client}-*  (from ConfigMap)
set -u

ROLE="${ROLE:?set ROLE=server|client}"
INTERVAL="${RECONCILE_INTERVAL:-300}"
HOST="nsenter -t 1 -m -u -i -n --"

log() { echo "[nut-reconcile $(hostname) role=$ROLE] $*"; }

# Write rendered content ($1) to a host path ($2) with mode ($3)/owner ($4),
# only when it differs. Sets CHANGED=1 on write.
host_write() {
  src="$1"; dest="$2"; mode="$3"; own="$4"
  if $HOST sh -c "cmp -s - '$dest'" < "$src" 2>/dev/null; then return 0; fi
  tmp="$dest.tmp.$$"
  if ! $HOST sh -c "cat > '$tmp'" < "$src"; then log "ERROR writing $dest"; return 1; fi
  $HOST sh -c "chmod $mode '$tmp'; chown $own '$tmp' 2>/dev/null || true; mv '$tmp' '$dest'"
  log "updated $dest"; CHANGED=1
}

ensure_pkg() { # $1=binary  $2=apt package
  $HOST sh -c "command -v '$1' >/dev/null 2>&1" && return 0
  log "installing $2 on host"
  $HOST sh -c "DEBIAN_FRONTEND=noninteractive apt-get update -qq && \
               DEBIAN_FRONTEND=noninteractive apt-get install -y -qq $2" \
    && CHANGED=1 || log "WARN: apt install $2 failed (will retry next pass)"
}

render() { # substitute password placeholders from env
  sed -e "s|__ADMIN_PW__|${ADMIN_PW:-}|g" \
      -e "s|__UPSMON_REMOTE_PW__|${UPSMON_REMOTE_PW:-}|g" \
      -e "s|__MONUSER_PW__|${MONUSER_PW:-}|g" "$1"
}

reconcile_server() {
  ensure_pkg upsd nut
  for f in nut.conf ups.conf upsd.conf upssched.conf; do
    render "/nut-config/server-$f" > "/tmp/$f"
    host_write "/tmp/$f" "/etc/nut/$f" 640 "root:nut"
  done
  render /nut-config/server-upsd.users   > /tmp/upsd.users
  host_write /tmp/upsd.users   /etc/nut/upsd.users   640 "root:nut"
  render /nut-config/server-upsmon.conf  > /tmp/upsmon.conf
  host_write /tmp/upsmon.conf  /etc/nut/upsmon.conf  640 "root:nut"
  render /nut-config/server-upssched-cmd > /tmp/upssched-cmd
  host_write /tmp/upssched-cmd /etc/nut/upssched-cmd 755 "root:root"

  $HOST systemctl enable --now nut-driver.target nut-driver-enumerator.path \
        nut-server nut-monitor >/dev/null 2>&1 || true
  if [ "${CHANGED:-0}" = 1 ]; then
    log "server config changed -> restarting NUT"
    $HOST systemctl restart nut-driver.target nut-server nut-monitor || true
  fi
  $HOST sh -c 'upsc ups ups.status 2>/dev/null' >/dev/null 2>&1 \
    || log "WARN: upsc ups not answering yet (UPS attached to this node?)"
}

reconcile_client() {
  ensure_pkg upsmon nut-client
  render /nut-config/client-nut.conf    > /tmp/nut.conf
  host_write /tmp/nut.conf    /etc/nut/nut.conf    640 "root:nut"
  render /nut-config/client-upsmon.conf > /tmp/upsmon.conf
  host_write /tmp/upsmon.conf /etc/nut/upsmon.conf 640 "root:nut"

  $HOST systemctl enable --now nut-monitor >/dev/null 2>&1 || true
  if [ "${CHANGED:-0}" = 1 ]; then
    log "client config changed -> restarting upsmon"
    $HOST systemctl restart nut-monitor || true
  fi
  $HOST sh -c 'upsc ups@10.99.5.12 ups.status 2>/dev/null' >/dev/null 2>&1 \
    || log "WARN: cannot read ups@10.99.5.12 yet"
}

log "starting reconcile loop (interval ${INTERVAL}s)"
while :; do
  CHANGED=0
  if [ "$ROLE" = server ]; then reconcile_server; else reconcile_client; fi
  log "pass complete (changed=${CHANGED:-0}); sleeping ${INTERVAL}s"
  sleep "$INTERVAL"
done
