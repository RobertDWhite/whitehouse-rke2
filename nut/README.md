# NUT — UPS-driven graceful cluster shutdown

Host-level power management for the cluster. On an extended power outage the whole
cluster (and the Synology NAS) shuts down cleanly instead of hard-cutting when the
battery finally dies.

> **Not GitOps-reconciled.** [NUT](https://networkupstools.org/) runs on the node
> operating systems, not in Kubernetes, so Argo CD does not manage it. These files
> are the source of truth; apply them with the scripts in [`install/`](install/)
> (same convention as `platform/controllers/nvidia-device-plugin/node50-join.md`).

## Topology

```
        Eaton 9PX 2000 RT
              │ USB
              ▼
      rke2-node-12  ── NUT server (driver + upsd :3493 + upsmon primary)
              │ TCP 3493 over the LAN
   ┌──────────┼───────────────┬───────────────┐
   ▼          ▼               ▼               ▼
 node-10   node-11 …      node-50         Synology (10.100.1.20)
 node-13   node-14        (upsmon          (DSM network-UPS
 node-15                   secondary)       client, monuser)
```

- **rke2-node-12** has the UPS on USB and runs the full NUT server
  (`usbhid-ups` driver → `upsd` → primary `upsmon`).
- Every other RKE2 node (10, 11, 13, 14, 15, 50) runs `upsmon` as a **secondary**,
  monitoring `ups@10.99.5.12`.
- The **Synology** connects to the same `upsd` as a DSM *network UPS server* client
  (Control Panel → Hardware & Power → UPS), authenticating as `monuser`.

## Shutdown logic

Timing lives in [`server/upssched.conf`](server/upssched.conf):

1. Power fails → UPS goes on battery → `ONBATT` starts a **180-second** timer.
2. Power returns first → `ONLINE` cancels the timer. **Brief blips do nothing.**
3. Still on battery at 180s → [`upssched-cmd`](server/upssched-cmd) runs
   `upsmon -c fsd`, setting the forced-shutdown flag.
4. `upsd` propagates FSD to every secondary; they run `SHUTDOWNCMD`
   (`shutdown -h +0`) and disconnect. The primary waits out `HOSTSYNC` (15s),
   then shuts down node-12 last.

The 180s threshold is deliberately well short of the UPS runtime (~11 min at the
current ~70% load), leaving ample headroom for a clean shutdown. Adjust the
`START-TIMER` value in `upssched.conf` to change how long an outage must last
before the cluster powers down.

## Layout

```
server/   configs for rke2-node-12 (the node with the UPS)
client/   configs for every other RKE2 node (upsmon secondary)
install/  setup-server.sh / setup-client.sh — run as root on each node
```

## Deploying

On **rke2-node-12** (generates + prints credentials to store in 1Password):

```bash
sudo ./install/setup-server.sh
```

On **each other RKE2 node**:

```bash
sudo UPSMON_REMOTE_PW='<from the server run>' ./install/setup-client.sh
```

On the **Synology**: Control Panel → Hardware & Power → UPS → Enable UPS support →
*Network UPS server* → `10.99.5.12`. DSM authenticates as `monuser`/`secret`
automatically.

## Secrets

`upsd.users` holds the NUT passwords and is **not** committed. The install script
generates random passwords for `admin` and `upsmon_remote` and prints them once —
store them in 1Password. `monuser` is `secret` (the fixed value Synology DSM uses
for a network UPS server). The committed `*.example` files carry `__PLACEHOLDER__`
tokens only.

## Verifying

```bash
# From the server node — UPS state and every connected client:
upsc ups
ss -tnp | grep :3493        # one row per secondary + the Synology + localhost
```
