# FreshRSS AI Setup (Business Intelligence Workflow)

This stack installs three FreshRSS extensions:
- `AI Assistant` (`xExtension-AIAssistant`)
- `Feed Digest` (`xExtension-FeedDigest`)
- `Word highlighter` (`xExtension-WordHighlighter`)

## Why AI responses can fail

The AI Assistant extension appends `/chat/completions` to the configured base URL.
For Ollama OpenAI-compatible mode, your base URL must include `/v1`.

Correct:
- `http://ollama.ai-stack.svc.cluster.local:11434/v1`

Incorrect examples:
- `http://ollama.ai-stack.svc.cluster.local:11434`
- `http://ollama.ollama.svc.cluster.local:11434`

## Required FreshRSS extension configuration

In FreshRSS, open:
- `Settings -> Extensions -> AI Assistant -> Configure`

Set:
- `OpenAI Base URL (Article)`: `http://ollama.ai-stack.svc.cluster.local:11434/v1`
- `OpenAI Base URL (Roundup)`: `http://ollama.ai-stack.svc.cluster.local:11434/v1`
- `OpenAI API Key`: any non-empty value for local Ollama (for example `dummy`)
- `OpenAI Model`: `llama3.1:8b` (or another installed model)
- `Temperature`: `0.2` to `0.7`
- `Max tokens (chars)`: keep realistic (`4096` to `16000` recommended)

Also ensure the user has the extension enabled:
- `Settings -> Extensions`
- Enable `AI Assistant`
- Enable `Feed Digest` (optional but recommended)

## Business intelligence workflow

1. Create categories by customer or market segment.
2. Route each customer feed into the corresponding category.
3. Use AI Assistant for per-article normalization:
- cleaner title
- executive summary
- tags for indexing
4. Use the category `Summarize` button to generate unread digests.
5. Optionally enable `mark_read_after_summary` when your process is stable.
6. For local models (for example `llama3.1:8b`), set Feed Digest batch size to `1` for reliability.

Recommended prompt shape:
- Require strict JSON.
- Ask for fields: `company`, `event_type`, `impact`, `urgency`, `confidence`, `next_action`.
- Keep summaries short and factual.

## Automated customer intelligence pipeline

For scheduled, customer-specific business intelligence scoring and alert generation, use:

- `/Users/robert/Documents/rke2/whitehouse-rke2/freshrss/bi-pipeline/README.md`

This adds:
- FreshRSS greader ingestion
- AI extraction with OpenAI-compatible endpoints (for example Ollama `/v1`)
- Customer profile scoring for health/spend/churn signals
- CronJob deployment templates and digest output files
- InfluxDB metrics for Grafana dashboards (see `observability/grafana_dashboards/freshrss-customer-intelligence.json`)

## In-cluster smoke tests

From a terminal:

```sh
kubectl -n freshrss exec deploy/freshrss -- \
  sh -lc 'curl -sS http://ollama.ai-stack.svc.cluster.local:11434/api/version'
```

```sh
kubectl -n freshrss exec deploy/freshrss -- \
  sh -lc "curl -sS -X POST http://ollama.ai-stack.svc.cluster.local:11434/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer dummy' \
  -d '{\"model\":\"llama3.1:8b\",\"messages\":[{\"role\":\"user\",\"content\":\"Return JSON with title summary tags\"}]}'"
```

If `/api/version` works but `/v1/chat/completions` fails, verify:
- model exists in Ollama (`/api/tags`)
- base URL includes `/v1`
- DNS name uses `ai-stack` namespace
