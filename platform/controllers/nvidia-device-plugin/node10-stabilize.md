# node-10 (DGX Spark) — stop it hanging and staying down

Run as **root on rke2-node-10** (`spark-5a67`, 10.99.5.10). Every step is
idempotent and has a verify line. Do them in order; step 3 restarts
`rke2-server` (etcd keeps quorum on node-11/14, the API gets sluggish for
~1 min). Total time about 5 minutes.

Background (2026-09-13): the box hung twice in 24 h — once from memory
exhaustion (a pod OOM-looped at 6 GiB nine times until the NVIDIA driver
returned `NV_ERR_NO_MEMORY`), once with no log at all while the SoC sat at
88–92 °C. Both times it stayed dead 9–12 h because nothing reboots it.

## 1. Arm the hardware watchdog (a hang reboots in 30 s instead of 9 h)

```bash
mkdir -p /etc/systemd/system.conf.d
cat > /etc/systemd/system.conf.d/watchdog.conf <<'EOF'
[Manager]
RuntimeWatchdogSec=30s
RebootWatchdogSec=2min
EOF
systemctl daemon-reexec
sleep 2
cat /sys/class/watchdog/watchdog0/state        # expect: active
systemctl show -p RuntimeWatchdogUSec          # expect: RuntimeWatchdogUSec=30s
```

The device is `sbsa_gwdt` (`/dev/watchdog0`, 10 s hardware timeout). systemd
pets it; if the kernel wedges, firmware resets the board.

## 2. Make memory pressure fail allocations instead of wedging the host

The Spark's CPU and GPU share one 128 GB pool and the box was running
`vm.overcommit_memory=1` (never refuse). Nothing in `/etc/sysctl.d` set it,
so this file wins at boot:

```bash
cat > /etc/sysctl.d/90-no-overcommit.conf <<'EOF'
# 2026-09-13: unified CPU/GPU memory - fail allocations instead of hanging the host
vm.overcommit_memory = 0
EOF
sysctl -p /etc/sysctl.d/90-no-overcommit.conf   # expect: vm.overcommit_memory = 0
```

## 3. Let the kubelet evict before the pool is exhausted

Today the kubelet has **no** eviction thresholds or system reservation
(defaults: `memory.available<100Mi`). Append to `/etc/rancher/rke2/config.yaml`
(there is no existing `kubelet-arg:` key — check first) and restart:

```bash
grep -q '^kubelet-arg:' /etc/rancher/rke2/config.yaml && echo "kubelet-arg already present - merge by hand" || {
cp -a /etc/rancher/rke2/config.yaml /etc/rancher/rke2/config.yaml.bak-$(date +%Y%m%d)
cat >> /etc/rancher/rke2/config.yaml <<'EOF'
# 2026-09-13: evict pods before the unified CPU/GPU pool is exhausted (two host hangs 09-12/09-13)
kubelet-arg:
  - eviction-hard=memory.available<6Gi
  - eviction-soft=memory.available<12Gi
  - eviction-soft-grace-period=memory.available=1m
  - system-reserved=memory=8Gi,cpu=2
EOF
systemctl restart rke2-server
}
```

Verify (give it ~60 s):

```bash
systemctl is-active rke2-server                                          # active
tr '\0' '\n' < /proc/$(pgrep -f 'kubelet --' | head -1)/cmdline | grep -E 'eviction|reserved'
/var/lib/rancher/rke2/bin/kubectl --kubeconfig /etc/rancher/rke2/rke2.yaml get node rke2-node-10   # Ready
```

## 4. Thermal cap (the part fans fix properly; this is the software floor)

The kernel reports `ACPI thermal: [Firmware Bug]: No valid trip points`, so
nothing throttles before the firmware cuts power. Until airflow is fixed,
cap the GPU clock so sustained inference cannot push it to the 
shutdown line (GPU was 81 °C with 10 °C of T.Limit margin at 56 W).

```bash
nvidia-smi -pm 1
nvidia-smi -q -d CLOCK | grep -A3 'Max Clocks'          # note the max Graphics clock
nvidia-smi -lgc 0,1500                                   # cap at 1.5 GHz; revert: nvidia-smi -rgc
nvidia-smi --query-gpu=clocks.max.gr,clocks.gr,temperature.gpu --format=csv
```

If `-lgc` is unsupported on this SKU it says so and nothing changes — skip it.
Optionally cap the CPUs too (they were 88–92 °C):

```bash
for p in /sys/devices/system/cpu/cpufreq/policy*; do
  echo $(( $(cat $p/cpuinfo_max_freq) * 80 / 100 )) > $p/scaling_max_freq
done
grep . /sys/devices/system/cpu/cpufreq/policy0/scaling_max_freq
```

(Not persistent across reboot by design — remove once fans are in.)

## 5. Monitor for a few days

From any machine with cluster access:

```bash
kubectl debug node/rke2-node-10 --profile=sysadmin --image=busybox:1.36 -q --attach -- sh -c \
 'sleep 2; chroot /host sh -c "cat /sys/class/watchdog/watchdog0/state; sysctl vm.overcommit_memory; \
  nvidia-smi --query-gpu=temperature.gpu,power.draw --format=csv,noheader; \
  for z in /sys/class/thermal/thermal_zone*; do cat \$z/temp; done | sort -n | tail -1; \
  journalctl -k -b 0 | grep -cE \"Killed process|NVRM.*NO_MEMORY\""'
```

Expected: `active`, `0`, GPU well under 80 °C, hottest zone under 85000, and
`0` OOM/NVRM events. Cluster-side changes that pair with this runbook went in
commit `7f645cd0` (Ollama context/keep-alive/limit, sdr-viewer-api limit and
batch size).
