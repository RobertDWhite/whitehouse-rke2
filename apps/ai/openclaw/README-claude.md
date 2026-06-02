# OpenClaw Claude Setup

This repo now exposes both of OpenClaw's Claude-related model paths:

- `anthropic/claude-sonnet-4-6` and `anthropic/claude-opus-4-6`
  - Uses `ANTHROPIC_API_KEY`
  - Best for stable API-backed Claude access
- `claude-cli/claude-sonnet-4-6` and `claude-cli/claude-opus-4-6`
  - Uses the official `claude` CLI login
  - Best for Claude Code / Pro / Max subscription-backed usage

## What is already in the manifests

- `ANTHROPIC_API_KEY` is expected in the `openclaw-api-keys` ExternalSecret.
- OpenClaw model aliases include:
  - `claude` -> `anthropic/claude-sonnet-4-6`
  - `claude-opus` -> `anthropic/claude-opus-4-6`
  - `claude-code` -> `claude-cli/claude-sonnet-4-6`
  - `claude-code-opus` -> `claude-cli/claude-opus-4-6`

## Important limitation

The stock `ghcr.io/openclaw/openclaw` image does not install the official
Claude Code CLI binary for you. To make `claude-cli/*` actually work in this
cluster, build a custom image from `Dockerfile.claude` and point the
OpenClawInstance at it.

## Build and push a Claude-enabled OpenClaw image

Example:

```bash
docker build -f openclaw/Dockerfile.claude \
  -t registry.white.fm/openclaw-claude:2026.4.19-beta.2 \
  .

docker push registry.white.fm/openclaw-claude:2026.4.19-beta.2
```

Then update `openclaw/10-openclawinstance.yaml`:

```yaml
spec:
  image:
    repository: registry.white.fm/openclaw-claude
```

Keep `openclaw/kustomization.yaml` on the matching tag.

## Login flow after deploy

Find the pod:

```bash
kubectl -n openclaw get pods
```

Open a shell:

```bash
kubectl -n openclaw exec -it <pod> -- sh
```

### Standard Claude API path

Nothing interactive is required beyond setting `ANTHROPIC_API_KEY`.

Verify:

```bash
openclaw models list --provider anthropic
```

### Claude Code path

1. Log in to Claude Code inside the pod:

```bash
claude auth login
```

For API-console billing instead of Claude subscription auth:

```bash
claude auth login --console
```

2. Ask OpenClaw to reuse the CLI auth:

```bash
openclaw models auth login --provider anthropic --method cli --set-default
```

3. Verify the CLI-backed models are visible:

```bash
openclaw models list --provider claude-cli --all
```

## Practical recommendation

- Use `anthropic/*` as the stable fallback.
- Use `claude-cli/*` when you specifically want Claude Code / Pro / Max-backed
  usage through the official CLI.
- Do not switch the primary model to `claude-cli/*` until the custom image is
  built, deployed, and authenticated successfully.
