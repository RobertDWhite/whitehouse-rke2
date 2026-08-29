# ComfyUI

Local AI image generation on `rke2-node-50` (RTX 5090), reachable at
`https://comfyui.internal.white.fm`.

## Why node-50

Diffusion is memory-bandwidth and compute bound. The 5090's 32 GB GDDR7 at
~1.8 TB/s is roughly 6x the bandwidth of node-10's GB10 unified memory, and the
node is amd64 so the cu128 torch wheels and most custom nodes are first-class.
node-10 also has to keep serving always-on Ollama inference, which image
generation would contend with.

The cost is that node-50 is a transient dual-boot host, so the Deployment carries
the `not-ready`/`unreachable` NoExecute tolerations (lesson: `project_node50_transient`).
When node-50 is off, ComfyUI is simply unavailable — it is not an HA workload.

### GPU sharing caveat

The nvidia device plugin time-slices node-50's GPU; there is no VRAM isolation.
`ollama-5090` runs there too with `OLLAMA_KEEP_ALIVE=2h`, so a large model can be
holding VRAM when ComfyUI tries to load a checkpoint, and one of the two will OOM.
If that becomes a problem, lower `OLLAMA_KEEP_ALIVE` on `ai-stack/12-ollama-5090-deployment.yaml`
or keep ComfyUI on smaller checkpoints.

## Egress

The namespace is default-deny egress. The only paths out are cluster DNS and
`vpn-proxy.media.svc.cluster.local` (HTTP CONNECT 8888 / SOCKS5 1080), so every
model download, ComfyUI-Manager fetch, `pip install` and `git clone` leaves through
the VPN exit. Anything that ignores the `*_PROXY` env vars fails closed at L3
rather than leaking the home IP.

Container image pulls are the one exception — those are done by kubelet at the node
level and are not subject to pod NetworkPolicy.

## Volumes

| Volume | Mount | Class | Backed up |
|---|---|---|---|
| `comfyui-run` | `/comfy/mnt` — venv, ComfyUI source, pip/HF caches | local-path | no |
| `comfyui-models` | `/basedir/models` — checkpoints, VAEs, LoRAs, upscalers | local-path | no |
| `comfyui-data` | `/basedir` — output, input, user settings, custom_nodes | local-path | yes |

local-path rather than Longhorn: the pod never leaves node-50, model weights are
re-downloadable, and Longhorn is capacity constrained. Velero picks up `comfyui`
from `daily.yaml` and the pod annotation excludes `run` and `models` so the daily
kopia backup does not try to ship hundreds of GB of weights.

## First start

The entrypoint clones ComfyUI and installs torch on first boot, which takes a while
over the VPN proxy — hence the 30 minute startupProbe budget. Once the pod is
healthy, set `DISABLE_UPGRADES: "true"` in `10-deployment.yaml` so later restarts
skip the pip upgrade pass and a wedged vpn-proxy cannot block startup.

Leave it `"false"` for the initial install: upstream's "new install forces upgrades
on" safety net is broken by a typo in `init.bash` (`"$A{DISABLE_UPGRADES}"`), so
setting it `true` on an empty volume would skip the torch install entirely.

## Model seeding

`comfyui-model-seed` is a PostSync hook Job that downloads the models listed in
`70-model-seed-configmap.yaml` (`models.txt`, one `<subdir> <filename> <url>` per
line). It skips files that already exist, so re-running it just picks up newly
added lines. A failed entry is reported but does not fail the whole Job.

Check the first run before assuming the defaults are right — the URLs were not
reachable from the authoring machine:

```bash
kubectl -n comfyui logs job/comfyui-model-seed
```

Gated repos (FLUX.1-dev, SD3.5) need an `Authorization: Bearer <hf_token>` header,
which this seeder does not send. Pull those through ComfyUI-Manager in the UI, or
add a SOPS secret and extend `seed.py`.

## Authentik SSO — one-time manual step

ComfyUI has no authentication of its own and can execute custom-node code, so the
HTTPRoute sends `comfyui.internal.white.fm` to the Authentik embedded outpost rather
than to the pod. Until the Proxy Provider exists, the hostname will not serve.

Create it once (see `platform/networking/technitium/bin/setup-authentik-sso.md` for
the full runbook):

1. Authentik → Applications → Providers → Create → **Proxy Provider**
   - Name: `Provider for comfyui SSO`
   - Mode: **Forward auth (single application)**
   - External host: `https://comfyui.internal.white.fm`
   - Internal host: `http://comfyui.comfyui.svc.cluster.local:8188`
2. Applications → Create → slug `comfyui-sso`, provider as above,
   launch URL `https://comfyui.internal.white.fm`.
3. Outposts → **authentik Embedded Outpost** → add the new application.

ComfyUI's UI is WebSocket-heavy; the outpost proxies WS fine, but note that anything
calling the ComfyUI HTTP API from outside the cluster will now hit the SSO redirect.
In-cluster consumers (Open WebUI) talk to the Service directly and are unaffected.

## Consumers

Open WebUI (`ai-stack`) is wired to this as its image-generation backend via
`COMFYUI_BASE_URL`. Those env vars only seed a fresh Open WebUI database — an
existing install also needs Admin Settings → Images pointed at ComfyUI by hand.

## Metrics

`comfyui-exporter` polls `/system_stats`, `/prompt` and `/queue` and exposes
`comfyui_up`, `comfyui_queue_{remaining,running,pending}` and per-device VRAM gauges
on `:9402/metrics`. Prometheus scrapes it via the `comfyui` job in
`observability/02-config-prometheus.yaml`.
