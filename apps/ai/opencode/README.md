# opencode

[opencode](https://github.com/anomalyco/opencode) coding agent, served as its
web UI (`opencode web`) at https://opencode.internal.white.fm.

- **Runs on the DGX Spark** (rke2-node-10, arm64), next to its model backend.
  The upstream image `ghcr.io/anomalyco/opencode` is multi-arch.
- **Models**: the Spark's Ollama (`ollama.ai-stack:11434/v1`), default
  `qwen3.8:27b`, small model `llama3.1:8b` — see `15-configmap.yaml`.
  Ollama runs `OLLAMA_NUM_PARALLEL=1`, so opencode turns queue behind
  Hermes/Riffado calls on the same instance.
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
