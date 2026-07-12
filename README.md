# whitehouse-rke2

A single-cluster, GitOps-managed [RKE2](https://docs.rke2.io/) Kubernetes homelab. Everything in this repository is the source of truth: [Argo CD](https://argo-cd.readthedocs.io/) continuously reconciles the live cluster against `main`, so the cluster *is* whatever this repo says it is. There is no manual `kubectl apply` workflow — you edit manifests, commit, push, and Argo CD does the rest.

The cluster hosts a broad mix of self-hosted services: a local LLM/AI stack on two NVIDIA GPUs, a software-defined-radio (SDR) capture-and-decode pipeline, social/fediverse apps, data-collection apps (congressional trades, politics, weather, app-store reviews), a fleet of custom Model Context Protocol (MCP) servers, plus the platform plumbing (DNS, identity, ingress, mesh, storage, backup, and observability) that ties it all together.

> **Cluster version:** RKE2 `v1.35.x` (one transient node trails a minor version). Control plane is **3-node HA etcd**.
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
- [Observability stack](#observability-stack)
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
              Cloudflared tunnel (ns: cloudflared)
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

RKE2 nodes are named `rke2-node-NN`. The cluster mixes one always-on arm64 GPU host, a transient high-end GPU host, and a set of amd64 nodes — several of which have USB SDR radios physically attached, which is why those workloads are pinned by `kubernetes.io/hostname`.

| Node | Role | Arch | Cores | RAM | GPU | Attached radios / function |
|------|------|------|-------|-----|-----|----------------------------|
| **rke2-node-10** | control-plane, etcd | arm64 | 20 | ~128 GB | 4× NVIDIA Spark — **always on** | Airspy SDR, VHF unified-SDR pipeline, **primary 24/7 AI inference** (Ollama, Immich ML) |
| **rke2-node-11** | control-plane, etcd | amd64 | 12 | ~48 GB | — | Utility node — Authentik replica, Uptime-Kuma |
| **rke2-node-12** | worker | amd64 | 8 | ~24 GB | — | **RX888** wideband HF SDR, HF decode chain (HFDL/FT8/WSPR/SSTV) |
| **rke2-node-13** | worker | amd64 | 8 | ~32 GB | — | **RTL-SDR** dongles (VHF + 70 cm + pager), AIS, VDL2 — busiest radio node; also congress-trades |
| **rke2-node-14** | control-plane, etcd | amd64 | 44 | ~48 GB | — | Data apps (congress-trades, politics, app-store-reviews, listmonk), Headscale, Postgres |
| **rke2-node-15** | worker | amd64 | 6 | ~8 GB | — | **ADS-B** feeder, Nitter, Authentik replica |
| **rke2-node-50** | worker | amd64 | 32 | ~48 GB | **4× RTX 5090** — **transient** (dual-boots, may be offline) | Larger-model inference, image generation, batch GPU jobs |

**GPU details.** Both GPU hosts run the `nvidia-device-plugin` with **separate ConfigMaps per node** for time-slicing. Cluster-wide:

- `RuntimeClass: nvidia` — every GPU pod must set `runtimeClassName: nvidia`.
- `PriorityClass: high-priority` (value 1000) so GPU workloads preempt CPU-only pods.
- `migStrategy: none` — time-slicing, not hardware MIG partitioning.

node-10 runs the NVIDIA-flavored kernel (DGX Spark / Grace platform); node-50 is an off-cluster GPU box that comes and goes.

**node-50 is treated as transient:** GPU pods pinned to it carry `node.kubernetes.io/not-ready` tolerations so they stay scheduled (rather than evicted to a non-GPU node) when the box reboots. The Ollama router (below) falls back to the always-on node-10 automatically.

**Build architecture:** most nodes are amd64 (`docker buildx --platform linux/amd64`); **node-10 is the lone arm64 host** — workloads pinned there are built `--platform linux/arm64` and tagged with an `-arm64` suffix.

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

**Cloudflared** runs the public-ingress tunnel; new public hostnames are normally added via an `HTTPRoute` (external-dns publishes the record) rather than editing the tunnel config.

**VPN egress proxy** (`media` namespace, Gluetun) gives any workload a non-home-IP egress path (HTTP CONNECT + SOCKS5) — used by the DNS upstream (ODoH) and threat-intel feeds. A watchdog CronJob restarts it if it wedges.

---

## Identity & mesh

**Authentik** (`authentik` namespace) is the single sign-on / OIDC provider for everything — Grafana, Technitium, Headscale, and many apps either speak OIDC natively or sit behind the Authentik embedded outpost. New OIDC clients are provisioned through the Authentik API (Grafana's provider is the canonical template) and the client credentials are persisted into the consuming namespace's SOPS secret.

**Headscale** (`headscale` namespace) is a self-hosted Tailscale control server providing the private mesh used to reach internal services. It authenticates users via Authentik OIDC, runs MagicDNS with split-DNS for the internal zone, and ships its own DERP relay.

---

## Storage & backup

- **Longhorn** — primary distributed block storage (replicated volumes).
- **Synology CSI** — NAS-backed volumes / snapshots for larger datasets.
- **snapshot-controller** — CSI volume snapshot support.
- **Velero** (`velero` namespace) — scheduled backups to a MinIO bucket, using CSI snapshots with data movement. A `daily-critical` schedule (7-day TTL) covers the stateful namespaces; a weekly schedule sweeps lower-churn namespaces. Velero does **not** back up the control plane, node OS, or git (Argo CD is git's source of truth). The `velero/recovery.md` runbook documents restore scenarios.

---

## Observability stack

Namespace `observability` (plus `uptime-kuma` and `myspeed` siblings). The stack:

- **Prometheus** — short scrape interval; static jobs for network polling, Velero, MinIO, Argo CD, CoreDNS, the weather API, and more, plus annotation-based discovery. Alertmanager routes notifications to **ntfy**.
- **Grafana** — OIDC via Authentik. Dashboards are version-controlled as JSON in `observability/observability/grafana_dashboards/` (a sidecar imports them on commit). The catalog includes dashboards for the SDR pipeline & station health, Technitium fleet, Envoy gateway ingress, Argo CD, cert-manager, Velero, MinIO, networking, Ollama, database health, and per-app dashboards.
- **Loki + Promtail** — log aggregation, including an RKE2 kube-audit pipeline.
- **InfluxDB** — time-series for network metrics.
- **Uptime-Kuma** with **AutoKuma** — synthetic uptime monitoring, monitors declared as config.
- **MySpeed** — periodic internet speed-test tracking.

---

## AI / GPU stack

Namespace `ai-stack`. A local, OpenAI-compatible LLM platform spanning both GPUs with automatic failover.

- **Ollama (node-10)** — the always-on backend on the arm64 GPU host. GPU time-slicing; good for chat-sized models 24/7.
- **Ollama-5090 (node-50)** — the high-VRAM RTX 5090 backend for larger models, kept scheduled across node-50's reboots via not-ready tolerations.
- **Ollama router** — an nginx reverse proxy presenting one Ollama endpoint. The **5090 is primary** (fast GDDR7); **node-10 is the always-on backup** (`max_fails=0` so a cold-model load never ejects it). `proxy_next_upstream` retries to the backup on upstream errors, so clients see a single stable endpoint regardless of whether the 5090 is powered on.
- **Open WebUI** — the chat UI, wired to both Ollama backends and to LocalAI for image generation; behind Authentik.
- **LocalAI / Stable Diffusion** — image-generation backend (currently scaled to 0; enabled on the 5090 when needed).
- **Ollama exporter** — Prometheus metrics for model load/latency, feeding the Ollama Grafana dashboard.

Local inference is also consumed *inside* the cluster — e.g. the SDR pipeline tags radio transcripts via Ollama, and the MCP servers below let LLM clients reach self-hosted data.

---

## Custom MCP servers

A fleet of self-built [Model Context Protocol](https://modelcontextprotocol.io/) servers (FastMCP, streamable-HTTP) exposes self-hosted services to LLM clients. All are **internal-only** (tailnet) and gated by a bearer token, with secrets held per-app in SOPS.

| MCP server | Wraps | What it does |
|------------|-------|--------------|
| **congress-mcp** | `congress-trades` API | Query congressional stock trades, member track records, leaderboards, signals, backtested follow-strategies, portfolio overlap |
| **freshrss-mcp** | FreshRSS (Google Reader API) | List feeds/categories, browse unread/starred articles, read full content, mark read/unread, star, mark-all-read, add subscriptions |
| **jetlog-mcp** | `jetlog` flight log | Read/add/analyze flights, parse boarding passes, enrich, airport/airline lookup, statistics |
| **media-mcp** | Plex, Tautulli & the *arr stack | Search libraries, sessions, queues, history, recently-added, and indexer stats across Plex/Tautulli/Sonarr/Radarr/Readarr/Bazarr/Prowlarr |
| **monica-mcp** | Monica CRM (CardDAV/CalDAV) | Contacts, important dates, tasks (via `/dav`, since Monica v5 dropped REST) |
| **nodebyte-mcp** | `nodebyte` inventory | Add/search/update inventory nodes (devices, sites, services), stats, and team listing |
| **sdr-research MCP** | SDR pipeline API | Query recordings, transcripts, decoded packets, and signal activity |

Each MCP ships a `NetworkPolicy` restricting it to just its upstream service.

---

## Custom application deployments

Beyond off-the-shelf charts, several apps are bespoke, built and (mostly) hosted from the internal registry:

- **sdr-research** (`apps/radio/sdr-research`) — the flagship custom system: a software-defined-radio capture/decode/transcribe/search pipeline. RTL-SDR / Airspy / RX888 radios run as DaemonSets pinned to the nodes they're physically plugged into (node-10, -12, -13), feeding decoders for FM/AM voice, CW, APRS, pager (POCSAG/FLEX), EAS, ACARS, VDL2, AIS, HFDL, FT8/WSPR, and SSTV. Voice clips are Whisper-transcribed and AI-tagged via the local Ollama, with a React web UI, Postgres store, Prometheus metrics, and an MCP server. A scrubbed open-source extraction lives in `sdr-research-oss/`.
- **weather** (`apps/data/weather`) — a custom weather dashboard (API + UI) that ingests APRS weather data from the SDR pipeline, with a "time machine" PVC for history.
- **astronomy** (`apps/radio/astronomy`) — a custom astronomy dashboard (API + UI) served on the tailnet.
- Plus other in-house data apps: **congress-trades** (the congressional trading tracker behind congress-mcp), **politics**, **appstore-reviews**, **jetlog**, **worldmonitor**, and **odysseus** (a ChromaDB-backed retrieval service).

---

## Application catalog

Apps are grouped under `apps/` by domain. A non-exhaustive map:

- **ai/** — ai-stack (Ollama/OpenWebUI), the MCP servers, odysseus, openclaw, codex-refresh, pages
- **data/** — congress-trades, politics, appstore-reviews, jetlog, weather, worldmonitor
- **media/** — Immich, Audiobookshelf, Kavita, Calibre-Web-Automated, FlexGet, MinIO / VPN egress
- **social/** — Matrix stack, Mastodon stack, Nitter, Convos
- **radio/** — sdr-research, adsb-stack, astronomy, ground-station, keeptrack, openhamclock
- **home/** — Homepage, Homarr, Glance, Homebridge, Scrypted, homeschool, backoffice, hub, nodebyte
- **misc/** — FreshRSS, SearXNG, Wallabag, Listmonk, ntfy, Monica, MISP, IntelOwl, Yeti, CyberChef, Gramps, gibt-es-gott, hermes, and more

---

## Repository layout

```
bootstrap/        Argo CD app-of-apps entrypoints (CRDs, repos, apps)
argocd/ argo-cd/  Argo CD install + Application/ApplicationSet definitions
platform/
  networking/     Envoy Gateway, Technitium DNS, Headscale, external-services, kube-vip
  controllers/    cert-manager issuer, external-dns, external-secrets (1Password), nvidia-device-plugin, registry
  storage/        Longhorn, Synology CSI, snapshot-controller, Velero
  policy/         NetworkPolicies, policy engine
security/         Authentik, CrowdSec, kube-bench
observability/    Prometheus, Grafana, Loki, InfluxDB, Uptime-Kuma, MySpeed
apps/             Workloads grouped by domain (ai, data, media, social, radio, home, misc)
sdr-research/     Build context for the custom SDR pipeline images
sdr-research-oss/ Open-source extraction of the SDR system
docs/             Restructure proposal & design notes
.claude/skills/   homelab operations skill (load-bearing runbook)
.sops.yaml        SOPS encryption rules
```

For deep operational runbooks (Authentik API recipes, cert migration, registry push workarounds, GPU debugging, the git workflow, common task patterns) see `.claude/skills/homelab/SKILL.md`.
