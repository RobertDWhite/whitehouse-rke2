# OpenClaw

This folder deploys one `OpenClawInstance` named `openclaw` in namespace `openclaw`.

Secrets are sourced from 1Password via External Secrets Operator.

Required setup:

- Install the `external-secrets` Argo app from `argo-cd/applications/external-secrets-app.yaml`.
- Apply shared 1Password config from `external-secrets-config/` (ClusterSecretStore + token secret).
- In 1Password, create an item named `openclaw-api-keys` with fields:
  - `OPENAI_API_KEY` (required)
  - `ANTHROPIC_API_KEY` (optional)
  - `TELEGRAM_BOT_TOKEN` (optional)

Model/providers:

- Primary model: OpenAI GPT-4.1 mini
- Additional configured providers: Anthropic + local Ollama (`ollama.ai-stack.svc.cluster.local:11434`)
- Telegram channel is enabled in `10-openclawinstance.yaml`

Quick commands:

```bash
kubectl apply -k openclaw
kubectl -n openclaw describe externalsecret openclaw-api-keys
kubectl -n openclaw get secret openclaw-api-keys
kubectl -n openclaw get openclawinstances
kubectl -n openclaw port-forward svc/openclaw 18789:18789
```
