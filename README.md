> ReadMe generated with Claude Opus 5.5

# whitehouse-rke2

A single-cluster, GitOps-managed [RKE2](https://docs.rke2.io/) Kubernetes homelab. Everything in this repository is the source of truth: [Argo CD](https://argo-cd.readthedocs.io/) continuously reconciles the live cluster against `main`, so the cluster *is* whatever this repo says it is. There is no manual `kubectl apply` workflow — you edit manifests, commit, push, and Argo CD does the rest.

The cluster hosts a broad mix of self-hosted services: a local LLM/AI stack on two NVIDIA GPUs, a software-defined-radio (SDR) capture-and-decode pipeline, social/fediverse apps, data-collection apps (congressional trades, politics, weather, app-store reviews), a fleet of custom Model Context Protocol (MCP) servers, plus the platform plumbing (DNS, identity, ingress, mesh, storage, backup, and observability) that ties it all together.

> **Cluster version:** RKE2 `v1.36.2` on every node (containerd 2.3, Ubuntu 24.04). Control plane is **3-node HA etcd** (node-10, -11, -14).
>
> Hostnames, IP addressing, tunnel IDs, keys, and other environment-specific identifiers are intentionally omitted from this document. They live (encrypted where sensitive) in the manifests and are not duplicated here.

---

## Table of contents

- [Architecture at a glance](#architecture-at-a-glance)
- [GitOps model (Argo CD)](#gitops-model-argo-cd)
- [Secrets: SOPS / ksops + 1Password](#secrets-sops--ksops--1password)
- [Nodes & hardware](#nodes--hardware)
- [Networking, DNS & ingress](#networking-dns--ingress)
- [Identity & mesh](#identity--mesh)
- [Storage & backup](#storage--backup)
- [Power (UPS)](#power-ups)
- [Observability stack](#observability-stack)
- [Security & policy](#security--policy)
- [AI / GPU stack](#ai--gpu-stack)
- [Custom MCP servers](#custom-mcp-servers)
- [Custom application deployments](#custom-application-deployments)
- [Application catalog](#application-catalog)
- [Repository layout](#repository-layout)

---

## Architecture at a glance

```
                         Internet
                            │
                            ▼
                     Cloudflare DNS  ◄── external-dns syncs from HTTPRoutes
                            │
                            ▼
              Cloudflared tunnels (ns: cloudflared)
                            │
                            ▼
                  Envoy Gateway  ── HTTPRoutes per app, TLS terminated here
                            │       (cert-manager + Let's Encrypt DNS-01)
        ┌───────────────────┼─────────────────────────┐
        ▼                   ▼                         ▼
    Authentik           Technitium DNS            Workloads
   (SSO / OIDC)     (internal zone,               sdr-research, ai-stack,
                     ODoH upstream via VPN)        media, social, data, MCP…
        │
   Headscale mesh ── private tailnet for internal access
   (+ in-cluster Tailscale subnet router & DERP relay)

   MetalLB (L2) ── LoadBalancer IPs for non-HTTP services
                   (DNS, SMTP, syslog, Ollama, InfluxDB, DERP…)
```

Three DNS planes coexist:

- **Public** — the public zones are Cloudflare-managed. `external-dns` watches `HTTPRoute`s and publishes records pointing at the Cloudflared tunnel.
- **Internal** — the internal zone is served authoritatively by **Technitium**, pointing at the gateway VIP. Reachable over the Headscale tailnet.
- **Cluster** — CoreDNS resolves `*.svc.cluster.local`; Technitium forwards cluster names back to CoreDNS.

The control-plane API is fronted by a **kube-vip** virtual IP (DaemonSet in `kube-system`) so the API server has a stable address independent of any single control-plane node.

---

## GitOps model (Argo CD)

Argo CD watches the GitHub repository and auto-syncs. The bootstrap is an **app-of-apps** pattern:

- `bootstrap/app-of-crds.yaml` — installs CRDs first (cert-manager, Gateway API, Argo, etc.).
- `bootstrap/app-of-repos.yaml` — registers Helm/Git repositories.
- `bootstrap/app-of-apps.yaml` — the root Application that fans out to every other Application.

Application manifests live in `argo-cd/applications/` (plus `argo-cd/app-configs/` and `argo-cd/applicationsets/`); roughly 120 Applications are managed today. A separate private repo (`whitehouse-glacier`) is pulled in as its own Application. ksops (v4.5.x) is installed into the repo-server and run through Argo CD's own kustomize. The application controllers are kept off node-11, whose API server has been prone to OOM loops that wedged syncs.

Most Applications run with `automated: { prune: true, selfHeal: true }`. **Consequences:**

- **Never `kubectl apply`/`edit`/`patch` directly** — selfHeal reverts manual changes within seconds. The only correct change path is *edit manifest → commit → push → Argo CD reconciles*.
- To preview what will apply: `kubectl kustomize --enable-alpha-plugins <dir>/`.
- For emergency live debugging you temporarily strip an Application's `syncPolicy`, then restore it before ending the session (a suspended app silently diverges from git).

Renovate (`.github/renovate.json`) opens grouped PRs for image/chart version bumps. Image tags live in each namespace's `kustomization.yaml` `images:` block (never hardcoded in Deployments), which is what wires them into Renovate automatically. Images served from the internal registry are **not** Renovate-covered (the registry isn't internet-reachable) and are bumped manually.

---

## Secrets: SOPS / ksops + 1Password

Two complementary secret systems are in play.

**SOPS + ksops (secrets-in-git).** Every namespace that needs secrets ships a `ksops.yaml` generator plus one or more `*.sops.yaml` files, encrypted at rest with [SOPS](https://github.com/getsops/sops) (age recipient) and decrypted at apply-time by the ksops kustomize plugin.

- Encryption rules live in `.sops.yaml` (per-path `encrypted_regex` so only `data` / `stringData` / `spec` blocks are encrypted, never the whole manifest where structure matters).
- Edit with `sops <file>.sops.yaml`; read one value with `sops --decrypt <file> | yq '.stringData.KEY'`. Never decrypt to disk — a pre-commit hook blocks plaintext siblings.

**External Secrets + 1Password (secrets-from-vault).** `platform/controllers/external-secrets-config` defines a `ClusterSecretStore` named `onepassword-shared` backed by a 1Password Connect token (itself SOPS-encrypted). Apps that prefer pulling live from 1Password reference it via an `ExternalSecret`:

```yaml
spec:
  secretStoreRef:
    kind: ClusterSecretStore
    name: onepassword-shared
```

This gives a clean split: bootstrap/identity material lives encrypted in git (SOPS), while rotatable app credentials can be sourced from 1Password without ever touching the repo.

**Git push identity** uses a dedicated, isolated deploy key so unattended pushes work without depending on an interactive agent.

---

## Nodes & hardware

RKE2 nodes are named `rke2-node-NN`. The cluster mixes one always-on arm64 GPU host (a DGX Spark), a transient RTX 5090 host, and a set of amd64 nodes — several of which have USB SDR radios (or the UPS) physically attached, which is why those workloads are pinned by `kubernetes.io/hostname`.

| Node | Role | Arch | Cores | RAM | GPU | Attached hardware / function |
|------|------|------|-------|-----|-----|-------------------------------|
| **rke2-node-10** | control-plane, etcd | arm64 | 20 | ~128 GB (unified with GPU) | DGX Spark GB10, time-sliced ×4 — **always on** | Airspy SDR, VHF unified-SDR pipeline, **primary 24/7 AI inference** (Ollama, opencode); busiest node by pod count |
| **rke2-node-11** | control-plane, etcd | amd64 | 12 | ~48 GB | — | Utility node — Authentik replica, Immich Postgres; Argo CD controllers, Headscale router/DERP kept off it |
| **rke2-node-12** | worker | amd64 | 8 | ~16 GB | — | **RX888** wideband HF SDR, HF decode chain (HFDL/FT8/WSPR/SSTV), **UPS USB link** (NUT server). Rebuilt Sept 2026; currently **NotReady**, and stateful apps (e.g. Fleet MySQL) avoid it |
| **rke2-node-13** | worker | amd64 | 8 | ~32 GB | — | **RTL-SDR** dongles (VHF + 70 cm + pager), AIS, VDL2 — busiest radio node; also data apps |
| **rke2-node-14** | control-plane, etcd | amd64 | 48 | ~56 GB | — | Data apps (congress-trades, politics, app-store-reviews, listmonk, condo), Immich server, Matrix Postgres, Homebridge/Scrypted, Headscale. Tainted `kube-vip=disabled:NoSchedule` |
| **rke2-node-15** | worker | amd64 | 6 | ~8 GB | — | **ADS-B** feeder, Nitter, jetlog, Authentik replica, Technitium replica |
| **rke2-node-50** | worker | amd64 | 32 | ~48 GB | **1× RTX 5090 (32 GB)**, time-sliced ×6 — **transient** (dual-boots, may be offline) | Larger-model inference, image generation (ComfyUI/InvokeAI), Whisper (Riffado), Immich ML. Tainted `dedicated=gpu:PreferNoSchedule` |

**GPU details.** Both GPU hosts run the `nvidia-device-plugin` with **separate ConfigMaps per node** for time-slicing (node-10 ×4, node-50 ×6 — time-slicing only gates scheduling, it doesn't partition VRAM). Cluster-wide:

- `RuntimeClass: nvidia` — every GPU pod must set `runtimeClassName: nvidia`.
- `PriorityClass: high-priority` (value 1000) so GPU workloads preempt CPU-only pods.
- `migStrategy: none` — time-slicing, not hardware MIG partitioning.

node-10 runs the NVIDIA-flavored kernel (DGX Spark / Grace platform). After two host hangs in Sept 2026 it was hardened (hardware watchdog, strict memory overcommit, kubelet eviction thresholds, clock cap) and its Ollama footprint was reduced — see `platform/controllers/nvidia-device-plugin/node10-stabilize.md`. node-50 is an off-cluster GPU box that comes and goes (`node50-join.md`).

**node-50 is treated as transient:** GPU pods pinned to it carry `node.kubernetes.io/not-ready` / `unreachable` tolerations so they stay scheduled (rather than evicted to a non-GPU node) when the box reboots. The Ollama router (below) falls back to the always-on node-10 automatically.

**Build architecture:** most nodes are amd64 (`docker buildx --platform linux/amd64`); **node-10 is the lone arm64 host** — workloads pinned there are built `--platform linux/arm64` and tagged with an `-arm64` suffix.

**Provisioning:** `provisioning/` holds the cloud-init / OVA tooling to stand a node up from a fresh Ubuntu cloud image. Host-level settings that must survive rebuilds (inotify sysctls, NUT) are applied by privileged DaemonSets in `platform/controllers/node-init` and `platform/controllers/nut` rather than by hand.

---

## Networking, DNS & ingress

**Envoy Gateway** (`platform/networking/envoy-gateway`) is the in-cluster ingress. It owns a single `Gateway` with HTTPS listeners per domain; each app contributes an `HTTPRoute` in its own namespace. TLS is terminated at the gateway.

**cert-manager** issues certificates via a `letsencrypt-dns` `ClusterIssuer` (production Let's Encrypt, DNS-01 over Cloudflare). The pattern is moving toward **one Certificate per service** (covering both the public and internal hostname) and away from legacy wildcards — smaller blast radius and cleaner Certificate Transparency entries.

**Technitium DNS** (`platform/networking/technitium`) is the internal authoritative resolver:

- Multi-pod StatefulSet with anti-affinity, avoids the transient GPU node, prefers 10 GbE nodes. Fronted by a VIP.
- Zone source of truth is a single SOPS-encrypted secret; a sidecar hot-reloads records via the Technitium API shortly after the secret changes. **Records are edited in git, not the web UI.**
- Web UI behind Authentik OIDC.
- **Upstream privacy:** uncached queries forward to an in-cluster `dnscrypt-proxy`, which makes **ODoH (Oblivious DNS-over-HTTPS)** queries *through the VPN egress proxy* — so external resolvers never see the home IP. Falls back to public resolvers only if the ODoH path fails.
- A cron job syncs DHCP leases from the router into DNS.

**MetalLB** (`platform/networking/metallb`, L2 mode) hands out LoadBalancer IPs for non-HTTP services — Technitium DNS (its own dedicated, non-auto-assigned pool), Postfix SMTP, syslog/Loki, InfluxDB, Ollama, the Headscale DERP relay, and others. CoreDNS gets extra config from `platform/networking/dns` (e.g. pinning the DERP hostname to its MetalLB VIP rather than the gateway).

**Cloudflared** runs the public-ingress tunnels (a second deployment serves a separate tunnel for another domain); new public hostnames are normally added via an `HTTPRoute` (external-dns publishes the record) rather than editing the tunnel config.

**VPN egress proxy** (`media` namespace, Gluetun) gives any workload a non-home-IP egress path (HTTP CONNECT + SOCKS5) — used by the DNS upstream (ODoH) and threat-intel feeds. A watchdog CronJob restarts it if it wedges.

---

## Identity & mesh

**Authentik** (`authentik` namespace) is the single sign-on / OIDC provider for everything — Grafana, Technitium, Headscale, and many apps either speak OIDC natively or sit behind the Authentik embedded outpost. New OIDC clients are provisioned through the Authentik API (Grafana's provider is the canonical template) and the client credentials are persisted into the consuming namespace's SOPS secret.

**Headscale** (`headscale` namespace) is a self-hosted Tailscale control server providing the private mesh used to reach internal services. It authenticates users via Authentik OIDC, runs MagicDNS with split-DNS for the internal zone, and ships its own DERP relay. A **Tailscale subnet router** (`platform/networking/tailscale`) advertises the cluster/home subnets into the tailnet. The router and DERP are kept off node-11 after its flapping caused tailnet DNS/relay outages for remote clients.

---

## Storage & backup

- **Longhorn** — primary distributed block storage (replicated volumes). Since Sept 2026 Immich's photo originals and its (now in-cluster) Postgres also live on dedicated Longhorn volumes, with sync CronJobs pushing mobile uploads back to the Synology.
- **Synology CSI** — NAS-backed volumes / snapshots for larger datasets.
- **local-path-provisioner** — node-local volumes for caches and scratch data.
- **snapshot-controller** — CSI volume snapshot support.
- **Velero** (`velero` namespace, chart 12.x / Velero 1.18) — scheduled backups to two S3 backup locations (in-cluster MinIO and Garage), using file-system backups with Kopia; no VolumeSnapshotLocation is rendered. Schedules in `platform/storage/velero/schedules/`:
  - `daily` (03:00, 7-day TTL) — the stateful namespaces, including Tautulli.
  - `weekly` (Sunday 02:00, 49-day TTL) — lower-churn namespaces.
  - `fleet` (03:30, 30-day TTL) — Fleet's MySQL, alongside an in-namespace `mysqldump` CronJob.

  Several apps (e.g. Riffado, Immich) additionally run logical `pg_dump` CronJobs to the Synology. Velero does **not** back up the control plane, node OS, or git (Argo CD is git's source of truth). `platform/storage/velero/recovery.md` documents restore scenarios.

---

## Power (UPS)

`platform/controllers/nut` installs [NUT](https://networkupstools.org/) onto each node's systemd via privileged DaemonSets, so the shutdown path keeps working even when the cluster itself is degraded. node-12 holds the UPS USB link and runs the NUT server; every other node (and the Synology) is a client that shuts down cleanly on an extended outage. A `nut-exporter` feeds the **UPS / Power** Grafana dashboard.

---

## Observability stack

Namespace `observability` (plus `uptime-kuma` and `myspeed` siblings). The stack:

- **Prometheus** — short scrape interval; static jobs for network polling, Velero, MinIO, Argo CD, CoreDNS, the weather API, the UPS, Claude Code usage stats, and more, plus annotation-based discovery. node-exporter, kube-state-metrics and metrics-server cover the cluster itself. Alertmanager routes notifications to **ntfy**.
- **Grafana** — OIDC via Authentik, Postgres-backed. Dashboards are version-controlled as JSON in `observability/observability/grafana_dashboards/` (a sidecar imports them on commit). The catalog includes dashboards for the SDR pipeline & station health, Technitium fleet, Envoy gateway ingress, Argo CD, cert-manager, Velero, MinIO, networking, Ollama, AI code assistants, UPS / power, Home Assistant, database health, and per-app dashboards. A clustered image renderer and a kiosk route (for Home Assistant panels) are also deployed.
- **Loki + Promtail** — log aggregation, including an RKE2 kube-audit pipeline and a syslog intake on a MetalLB IP.
- **InfluxDB + UniFi Poller** — time-series for UniFi network metrics; Pi-hole exporters.
- **Uptime-Kuma** with **AutoKuma** — synthetic uptime monitoring, monitors declared as config.
- **MySpeed** — periodic internet speed-test tracking.

---

## Security & policy

- **Kyverno** (+ `kyverno-policies`, `platform/policy/policy`) — admission policy, including the allow-list of image registries (new upstream registries must be added there).
- **NetworkPolicies** — per-namespace policies in `platform/policy/network-policies`, plus per-app policies shipped with each app.
- **Falco** — runtime threat detection; falcosidekick posts alerts into Matrix via Hookshot.
- **CrowdSec** — behavioural detection / blocklists.
- **Trivy Operator** and **kube-bench** — image vulnerability and CIS benchmark reports.

`SECURITY-REVIEW.md` captures the most recent review of the cluster's security posture.

---

## AI / GPU stack

Namespace `ai-stack`. A local, OpenAI-compatible LLM platform spanning both GPUs with automatic failover.

- **Ollama (node-10)** — the always-on backend on the arm64 GPU host. GPU time-slicing; good for chat-sized models 24/7.
- **Ollama-5090 (node-50)** — the high-VRAM RTX 5090 backend for larger models, kept scheduled across node-50's reboots via not-ready tolerations.
- **Ollama-CPU** — a low-priority CPU-only instance anti-affined from the GPU backends, as a last-resort fallback.
- **Ollama router** — an nginx reverse proxy presenting one Ollama endpoint. The **5090 is primary** (fast GDDR7); **node-10 is the always-on backup** (`max_fails=0` so a cold-model load never ejects it). `proxy_next_upstream` retries to the backup on upstream errors, so clients see a single stable endpoint regardless of whether the 5090 is powered on.
- **Open WebUI** — the chat UI, wired to both Ollama backends and to LocalAI for image generation; behind Authentik.
- **LocalAI / Stable Diffusion** — legacy image-generation backend (scaled to 0).
- **Ollama exporter** — Prometheus metrics for model load/latency, feeding the Ollama Grafana dashboard.

Other GPU / AI workloads in their own namespaces:

- **InvokeAI** (`apps/ai/invokeai`) — the default front door for image generation, on the 5090.
- **ComfyUI** (`apps/ai/comfyui`) — node-graph image generation alongside InvokeAI, also on the 5090.
- **Riffado** (`apps/ai/riffado`) — Plaud Note recorder companion: syncs recordings, transcribes them through a GPU **speaches** (faster-whisper `large-v3-turbo`) instance kept resident on the 5090, stores audio/transcripts on the NAS.
- **opencode** (`apps/ai/opencode`) — the opencode coding agent's web UI, running on the Spark next to its Ollama backend, with this repo cloned into its workspace.
- **Hermes** (`apps/misc/hermes`) and **OpenClaw** (with its operator) — LLM agents that use the local models and MCP servers.

Local inference is also consumed *inside* the cluster — e.g. the SDR pipeline tags radio transcripts via Ollama, and the MCP servers below let LLM clients reach self-hosted data.

---

## Custom MCP servers

A fleet of self-built [Model Context Protocol](https://modelcontextprotocol.io/) servers (FastMCP, streamable-HTTP) exposes self-hosted services to LLM clients. All are **internal-only** (tailnet) and gated by a bearer token, with secrets held per-app in SOPS.

| MCP server | Wraps | What it does |
|------------|-------|--------------|
| **congress-mcp** | `congress-trades` API | Query congressional stock trades, member track records, leaderboards, signals, backtested follow-strategies, portfolio overlap |
| **gsc-mcp** | Google Search Console & IndexNow | Site/sitemap management, verification, search analytics, URL inspection, IndexNow submission |
| **freshrss-mcp** | FreshRSS (Google Reader API) | List feeds/categories, browse unread/starred articles, read full content, mark read/unread, star, mark-all-read, add subscriptions |
| **googlenews-mcp** | Google News (public RSS) | Top/topic/geo headlines, full-text news search, redirect-URL decoding — news access for internet-isolated agents |
| **jetlog-mcp** | `jetlog` flight log | Read/add/analyze flights, parse boarding passes, enrich, airport/airline lookup, statistics |
| **media-mcp** | Plex, Tautulli, the *arr stack & FileBot | Search libraries, sessions, queues, history, recently-added, indexer stats, and controlled FileBot CLI operations |
| **meerkat-mcp** | Meerkat CRM (REST API) | Contacts, notes, activities, reminders, and relationships — Meerkat is the Monica replacement |
| **monica-mcp** | Monica CRM (CardDAV/CalDAV) | Contacts, important dates, tasks (via `/dav`, since Monica v5 dropped REST); kept while Monica is still deployed |
| **nodebyte-mcp** | `nodebyte` inventory | Add/search/update inventory nodes (devices, sites, services), stats, and team listing |
| **sdr-research MCP** | SDR pipeline API | Query recordings, transcripts, decoded packets, and signal activity |

Each MCP ships a `NetworkPolicy` restricting it to just its upstream service.

---

## Custom application deployments

Beyond off-the-shelf charts, several apps are bespoke, built and (mostly) hosted from the internal registry:

- **sdr-research** (`apps/radio/sdr-research`) — the flagship custom system: a software-defined-radio capture/decode/transcribe/search pipeline. RTL-SDR / Airspy / RX888 radios run as DaemonSets pinned to the nodes they're physically plugged into (node-10, -12, -13), feeding decoders for FM/AM voice, CW, APRS, pager (POCSAG/FLEX), EAS, ACARS, VDL2, AIS, HFDL, FT8/WSPR, and SSTV. Voice clips are Whisper-transcribed and AI-tagged via the local Ollama, with a React web UI, Postgres store, Prometheus metrics, and an MCP server. Dockerfiles and source for the custom images (API, MCP, decoders, Soapy remotes) live alongside the manifests.
- **weather** (`apps/data/weather`) — a custom weather dashboard (API + UI) that ingests APRS weather data from the SDR pipeline, with a "time machine" PVC for history.
- **astronomy** (`apps/radio/astronomy`) — a custom astronomy dashboard (API + UI) served on the tailnet.
- **face-recognition** (`apps/home/face-recognition`) — CompreFace + Double Take for doorbell face greetings in Home Assistant (CPU-only; the CompreFace GPU builds don't run on either GPU node).
- **watch-badges** and **raven** — small static dashboards served from nginx.
- Plus other in-house data apps: **congress-trades** (the congressional trading tracker behind congress-mcp), **politics**, **appstore-reviews**, **jetlog**, **worldmonitor**, and **odysseus** (a ChromaDB-backed retrieval service).
- The custom MCP servers above (and `gsc-mcp` / `meerkat-mcp` images built by CI in their own GitHub repos, pulled from GHCR).

---

## Application catalog

Apps are grouped under `apps/` by domain. A non-exhaustive map:

- **ai/** — ai-stack (Ollama/OpenWebUI), ComfyUI, InvokeAI, Riffado, opencode, the MCP servers, odysseus, openclaw, codex-refresh, pages
- **data/** — congress-trades, politics, appstore-reviews, jetlog, weather, worldmonitor
- **media/** — Immich, Audiobookshelf, Kavita, Calibre-Web-Automated (+ LazyLibrarian, Shelfmark), Dispatcharr, Tautulli, the *arr stack / FlexGet / VPN egress (`media`); Ombi and Portainer are chart-only Applications
- **social/** — Matrix stack, Mastodon stack, Nitter, Convos
- **radio/** — sdr-research, adsb-stack, astronomy, ground-station, keeptrack, openhamclock
- **home/** — Homepage, Homarr, Glance, Homebridge, Scrypted, face-recognition, condo, homeschool, backoffice, hub, nodebyte
- **misc/** — Fleet (device management, incl. VPP app version refresh), FreshRSS, SearXNG, Wallabag, Listmonk, Postfix (SMTP relay), ntfy, Meerkat, Monica, MISP, IntelOwl, Yeti, CyberChef, Gramps, gibt-es-gott, hermes, raven, watch-badges

---

## Repository layout

```
bootstrap/        Argo CD app-of-apps entrypoints (CRDs, repos, apps)
argocd/           Argo CD install (Helm chart + values, ksops, repo/age secrets)
argo-cd/          Application / ApplicationSet / app-config definitions, CRDs, Helm repos
platform/
  networking/     Envoy Gateway, Technitium DNS, CoreDNS extras, MetalLB, Cloudflared,
                  Headscale, Tailscale subnet router, external-services, kube-vip
  controllers/    cert-manager issuer, external-dns, external-secrets (1Password),
                  nvidia-device-plugin, registry, metrics-server, NUT, node-init
  storage/        Longhorn, Synology CSI, snapshot-controller, Velero (+ schedules)
  policy/         NetworkPolicies, policy engine
security/         Authentik, CrowdSec, Falco, kube-bench
observability/    Prometheus, Grafana, Loki, InfluxDB, Uptime-Kuma, MySpeed
apps/             Workloads grouped by domain (ai, data, media, social, radio, home, misc)
provisioning/     Node cloud-init / OVA build tooling
clients/          Client apps (native SwiftUI Matrix client for iOS)
.github/          Renovate config, Dependabot, CI workflows
.sops.yaml        SOPS encryption rules
SECURITY-REVIEW.md  Latest security review
```
