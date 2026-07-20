# nut — UPS-driven graceful cluster shutdown

On an extended power outage the whole cluster (and the Synology NAS) shuts down
cleanly instead of hard-cutting when the battery dies.

## Why this shape

[NUT](https://networkupstools.org/) is a *host* service — its job is to power the
physical machine off, which a container cannot own. But we still want it managed
by GitOps rather than hand-installed. So this component is a **privileged
DaemonSet that installs and configures NUT on each node's `systemd`** (the same
pattern as `platform/controllers/node-init` and the GPU operator), while the NUT
runtime stays on the host. Result: the shutdown path keeps working even when the
cluster itself is degraded, and the config is still declarative + self-healing.

## What runs where

```
        Eaton 9PX 2000 RT
              │ USB
              ▼
      rke2-node-12  ── NUT server (usbhid-ups + upsd :3493 + upsmon primary)
              │ TCP 3493 over the LAN
   ┌──────────┼───────────────┬───────────────┐
   ▼          ▼               ▼               ▼
 node-10   node-11 …      node-50         Synology (10.100.1.20)
 node-13   node-14        (upsmon          (DSM network-UPS
 node-15                   secondary)       client, monuser)
```

- **`nut-installer-server`** DaemonSet — pinned to `rke2-node-12` (the node with
  the UPS on USB). Installs `nut`, writes the server config, enables the driver +
  `upsd` + primary `upsmon`.
- **`nut-installer-client`** DaemonSet — every other node. Installs `nut-client`,
  writes an `upsmon` secondary pointed at `ups@10.99.5.12`.

Both run [`files/reconcile.sh`](files/reconcile.sh), which `nsenter`s into PID 1,
writes config only when it differs (restarting NUT on change), and loops every
5 minutes to self-heal drift.

## Shutdown logic

Timing lives in [`files/server-upssched.conf`](files/server-upssched.conf):
on battery for **180 s continuously** → `upsmon -c fsd` → coordinated shutdown
(secondaries first, node-12 last). Power restored before 180 s cancels the timer,
so brief blips are ignored. 180 s is well short of the ~11 min runtime, leaving
headroom for a clean stop. Change the `START-TIMER` value to adjust.

## Files

```
kustomization.yaml     resources + ksops + configMapGenerator(nut-config)
00-namespace.yaml      Namespace nut
daemonset.yaml         nut-installer-server / nut-installer-client
ksops.yaml             decrypts nut-secret.sops.yaml
nut-secret.sops.yaml   NUT passwords (age-encrypted; admin / upsmon_remote / monuser)
files/                 reconcile.sh + server-*/client-* NUT config bodies
```

## Secrets

`nut-secret.sops.yaml` is SOPS/age-encrypted (repo recipient) and materialised by
ksops into the `nut-secrets` Secret. `reconcile.sh` substitutes the passwords into
the host config at write time, so the ConfigMap (`files/*`) carries only
`__PLACEHOLDER__` tokens. `monuser`/`secret` is the fixed account DSM uses for a
network UPS server. To rotate: edit the decrypted secret, re-`sops --encrypt`,
commit — the next reconcile pass rewrites and restarts NUT.

## Synology

Not managed here (it's an appliance). Control Panel → Hardware & Power → UPS →
Enable UPS support → *Network UPS server* → `10.99.5.12` (auth `monuser`/`secret`).

## Metrics / Grafana

`upsd` permits anonymous `LIST VAR`, so a plain exporter is enough — no credentials.
`nut-exporter` (Deployment + Service in `observability`) reads `ups@10.99.5.12` and
Prometheus scrapes it as job `nut`; the dashboard is **UPS / Power** (`ups-power`).
Both live in `observability/observability/`. The exporter only exposes a thin
default subset of variables, so the ones worth graphing are named explicitly in
`NUT_EXPORTER_VARIABLES` — add to that list to graph anything new from `upsc ups`.

## Verify

```bash
# on rke2-node-12:
upsc ups                    # OL = online
ss -tnp | grep :3493        # one row per secondary + the Synology + localhost
# pod logs:
kubectl -n nut logs -l app.kubernetes.io/name=nut --prefix --tail=20
```
