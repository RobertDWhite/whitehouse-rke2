# opencode

[opencode](https://github.com/anomalyco/opencode) coding agent, served as its
web UI (`opencode web`) at https://opencode.internal.white.fm.

- **Runs on the DGX Spark** (rke2-node-10, arm64), next to its model backend.
  The upstream image `ghcr.io/anomalyco/opencode` is multi-arch.
- **Models**: defaults to Qwen3.8 Flash Next through the in-cluster vLLM API
  (`qwen-flash.qwen-flash:8000/v1`) for both coding and lightweight tasks — see
  `15-configmap.yaml`. The Ollama provider remains available for manually
  selecting Qwen3.8 27B or Qwen3 0.6B through the Mac mini primary, with the
  RTX 5090 as failover.
- **Auth**: HTTP basic auth, user `opencode`, password in `11-secret.sops.yaml`
  (copy at `~/kube/secrets/opencode-password.txt`). Internal-only route.
- **Storage**: `opencode-home` PVC is `$HOME` (`/root`): sessions,
  `auth.json` (keys added with `/connect`), provider npm cache, and the
  working directory `/root/workspace`. Clone repos there. The public
  `whitehouse-rke2` repo is cloned at startup (`git pull --ff-only` on restarts;
  read-only, no push credentials).
- Startup `apk add`s git/ssh/bash/curl/jq (upstream image is bare alpine).

Attach a terminal TUI to the same sessions:

```bash
kubectl -n opencode port-forward svc/opencode 4096:4096
OPENCODE_SERVER_PASSWORD=$(cat ~/kube/secrets/opencode-password.txt) \
  opencode attach http://localhost:4096
```
