# InvokeAI

Local AI image generation on `rke2-node-50` (RTX 5090), reachable at
`https://invokeai.internal.white.fm`. **This is the default front door for image
generation**; ComfyUI stays deployed alongside as the node-graph power tool.

## Why this alongside ComfyUI

InvokeAI is a form-based UI — prompt box, settings sidebar, gallery — plus a
unified canvas that is the strongest img2img/inpainting workflow in the local
ecosystem. ComfyUI is more capable but the node graph is a real learning curve
for routine generation.

Practical differences that matter operationally:

| | InvokeAI | ComfyUI |
|---|---|---|
| Image | official `ghcr.io/invoke-ai/invokeai`, semver-tagged | community `mmartial/comfyui-nvidia-docker` |
| First boot | deps baked in, ~minutes | clones repo + pip installs torch, ~30 min |
| Volumes | 2 (root + models) | 3 (run + basedir + models) |
| Models | built-in model manager, browse and install in the UI | seed Job or ComfyUI-Manager |

InvokeAI needs no runtime volume because its venv ships in the image, which is
also why its startupProbe budget is 10 minutes rather than 30.

## Why node-50

Same reasoning as ComfyUI: diffusion is bandwidth and compute bound, and the
5090's 32 GB GDDR7 at ~1.8 TB/s is roughly 6x node-10's GB10 unified memory. The
node is a transient dual-boot host, so the Deployment carries the
`not-ready`/`unreachable` NoExecute tolerations and this is not an HA workload.

### GPU sharing caveat

node-50's GPU is time-sliced with no VRAM isolation, and there are now three
consumers: `ollama-5090`, `comfyui` and `invokeai`. Four slices are available so
scheduling is fine, but VRAM is not partitioned — two large models resident at
once will OOM one of them. In practice: do not drive InvokeAI and ComfyUI hard at
the same time, and consider lowering `OLLAMA_KEEP_ALIVE` on
`ai-stack/12-ollama-5090-deployment.yaml` if collisions become routine.

## Egress

The namespace is default-deny egress. The only paths out are cluster DNS and
`vpn-proxy.media.svc.cluster.local` (HTTP CONNECT 8888 / SOCKS5 1080), so every
model-manager download leaves through the VPN exit. Anything ignoring the
`*_PROXY` env vars fails closed at L3 rather than leaking the home IP. Container
image pulls are the exception — kubelet does those at node level, outside pod
NetworkPolicy.

## Volumes

| Volume | Mount | Class | Backed up |
|---|---|---|---|
| `invokeai-data` | `/invokeai` — outputs, sqlite DBs, configs, custom nodes | local-path | yes |
| `invokeai-models` | `/invokeai/models` — checkpoints, LoRAs, VAEs, ControlNets | local-path | no |

The models volume is excluded via the pod's `backup.velero.io/backup-volumes-excludes`
annotation so the daily kopia run does not ship hundreds of GB of re-downloadable
weights. The root volume holds the gallery database, which is the part you would
actually miss.

## Getting models

Use the built-in model manager in the UI (Models tab → Starter Models) rather than
a seed Job. It downloads through the VPN proxy like everything else here.

Gated repos (FLUX.1-dev, SD3.5) need a HuggingFace token — InvokeAI takes one at
Models → Settings, or via a `HF_TOKEN` env var if you would rather add a SOPS
secret to this namespace.

## Authentik SSO — one-time manual step

InvokeAI has no authentication enabled by default, so the HTTPRoute sends
`invokeai.internal.white.fm` to the Authentik embedded outpost rather than to the
pod. Until the Proxy Provider exists, the hostname will not serve.

1. Authentik → Applications → Providers → Create → **Proxy Provider**
   - Name: `Provider for invokeai SSO`
   - Mode: **Forward auth (single application)**
   - External host: `https://invokeai.internal.white.fm`
   - Internal host: `http://invokeai.invokeai.svc.cluster.local:9090`
2. Applications → Create → slug `invokeai-sso`, provider as above,
   launch URL `https://invokeai.internal.white.fm`.
3. Outposts → **authentik Embedded Outpost** → add the new application.

## Open WebUI

Open WebUI is **not** wired to InvokeAI and cannot be. It implements exactly four
image engines — `openai`, `gemini`, `comfyui`, `automatic1111` — and InvokeAI
exposes neither an A1111- nor an OpenAI-images-compatible API; it has its own
`/api/v1` queue/graph API. Chat-driven image generation therefore stays on
ComfyUI. Changing that needs a translation shim in front of InvokeAI, which is
real custom code and not currently worth the maintenance.

## Metrics

`invokeai-exporter` polls `/api/v1/app/version` and `/api/v1/queue/default/status`
and exposes `invokeai_up`, `invokeai_info` and `invokeai_queue_*` gauges on
`:9403/metrics`. Prometheus scrapes it via the `invokeai` job in
`observability/02-config-prometheus.yaml`.

Note there is no VRAM gauge here — unlike ComfyUI's `/system_stats`, InvokeAI has
no unauthenticated device-stats endpoint.
