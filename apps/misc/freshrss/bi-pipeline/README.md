# FreshRSS Customer Intelligence Pipeline

This pipeline turns FreshRSS unread items into customer-specific intelligence alerts for Customer Success.

## What it does

1. Pulls unread/news items from FreshRSS greader API.
2. Optionally enriches articles with fetched full text (for stronger AI extraction quality).
3. Clusters near-duplicate stories and tracks per-story novelty.
4. Extracts richer event + impact schema with an OpenAI-compatible model endpoint (Ollama works).
5. Scores each event per customer profile (signals, context terms, novelty, renewal pressure, source quality).
6. Routes to alerts vs watchlist using confidence gates + cooldown suppression.
7. Supports dynamic per-customer thresholds and no-signal coverage monitoring.
8. Ingests human feedback labels to improve source/customer/event weighting over time.
9. Writes structured JSON + markdown digest and optionally posts a webhook payload.
10. Supports optional action hooks for high-score alerts.
11. Tracks customer story timeline deltas and account heat forecasts (30d/90d).
12. Adds Cyera AI/DSPM relationship-risk evaluation (summary + risk score/label) per item.
13. Optionally writes expanded metrics to InfluxDB for Grafana dashboards.

## Folder layout

- `pipeline.py`: main runner.
- `config.example.yaml`: local starter config.
- `requirements.txt`: Python dependencies.
- `Dockerfile`: container image build.
- `k8s/`: example Kubernetes manifests (CronJob + ConfigMap + Secret + PVC).

## Local quick start

```bash
cd /Users/robert/Documents/rke2/whitehouse-rke2/freshrss/bi-pipeline
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml
```

Update `config.yaml` for your FreshRSS API credentials and customer profiles.

Run once:

```bash
python pipeline.py --config ./config.yaml --print-json
```

Dry run (no state update, no webhook):

```bash
python pipeline.py --config ./config.yaml --dry-run
```

## FreshRSS API settings

Use a FreshRSS account API password. The script authenticates via:

- `GET /api/greader.php/accounts/ClientLogin`
- `GET /api/greader.php/reader/api/0/stream/contents/user/-/state/com.google/reading-list`

Set `fresh_rss.greader_api_url` to your full endpoint, for example:

- `https://freshrss.example.com/api/greader.php`

Retry controls (recommended for intermittent API timeouts):

- `fresh_rss.retry_attempts` (default `3`)
- `fresh_rss.retry_backoff_seconds` (default `2`)
- `fresh_rss.page_size` (default `100`, used with stream continuation paging)

## AI endpoint settings

For Ollama in OpenAI-compatible mode, set:

- `ai.base_url`: `http://ollama.ai-stack.svc.cluster.local:11434/v1`
- `ai.model`: your installed model, for example `llama3.1:8b`
- `ai.api_key`: any non-empty value if your endpoint expects an Authorization header

If AI fails, the pipeline falls back to heuristic extraction so the run still completes.

Retry controls for slow/inconsistent model endpoints:

- `ai.retry_attempts` (default `2`)
- `ai.retry_backoff_seconds` (default `1.5`)

## BI controls

- `enrichment.*`: controls full-text fetch enrichment.
- `dynamic_thresholds.*`: enables percentile-driven per-customer thresholds.
- `alert_routing.*`: controls confidence gates, cooldowns, watchlist floor, and no-signal windows.
- `feedback.*`: consumes JSONL labels (relevant/not_relevant/etc.) for adaptive source/event/customer multipliers.

## Kubernetes deployment

Edit these files first:

- `k8s/configmap.sops.yaml` (customer profiles, thresholds, endpoint URLs)
- `k8s/secret.example.yaml` (credentials)
- `k8s/cronjob.yaml` (container image)
- `k8s/pvc.yaml` (storage class/size)

Apply:

```bash
kubectl apply -k /Users/robert/Documents/rke2/whitehouse-rke2/freshrss/bi-pipeline/k8s
```

Run immediately from CronJob template:

```bash
kubectl -n freshrss create job --from=cronjob/freshrss-bi-pipeline freshrss-bi-pipeline-manual-$(date +%s)
```

## Outputs

Per run (timestamped folder):

- `events.json`: full analyzed payload.
- `alerts.json`: customer alerts above threshold.
- `digest.md`: human-readable summary by customer (alerts + watchlist + coverage gaps).
- `timeline.json`: per-customer story additions/changes/removals vs prior snapshot.
- `triage_queue.json`: watchlist + needs-data queue for human review.

State file tracks seen article IDs to avoid duplicate alerts across runs.
If InfluxDB is unavailable, the run still completes and state is still updated.

## Config notes

- Config values support environment interpolation like `${FRESHRSS_API_PASSWORD}`.
- Keep customer keyword sets specific; fewer high-quality terms usually improve precision.
- Tune `min_alert_score` per customer to reduce noise. Values are clamped to `0-100`.
- Optional retention controls:
  - `output.retention_days` (age-based pruning, `0` disables)
  - `output.max_run_directories` (count-based pruning, `0` disables)

## Grafana analytics (recommended GUI)

The pipeline can write run/customer/alert metrics into InfluxDB.

1. Enable and set `influxdb` in config:
   - `enabled: true`
   - `url`: `http://influxdb.observability.svc.cluster.local:8086`
   - `org`, `bucket`, `token`
2. In Kubernetes, set these secret keys in `k8s/secret.example.yaml`:
   - `INFLUXDB_URL`
   - `INFLUXDB_ORG`
   - `INFLUXDB_BUCKET`
   - `INFLUXDB_TOKEN`
3. Run the pipeline at least once so measurements are created:
   - `freshrss_bi_run`
   - `freshrss_bi_customer_summary`
   - `freshrss_bi_alert`
   - `freshrss_bi_event_type_summary`
   - `freshrss_bi_source_summary`
   - `freshrss_bi_model_health`
   - `freshrss_bi_noise_summary`
   - `freshrss_bi_coverage_gap`
   - `freshrss_bi_feedback_summary`
   - `freshrss_bi_outcome_summary`
4. Import dashboard JSON in Grafana:
   - `/Users/robert/Documents/rke2/whitehouse-rke2/observability/grafana_dashboards/freshrss-customer-intelligence.json`
5. On import, pick your InfluxDB datasource and bucket variable.

`freshrss_bi_run` also includes run-health fields such as:

- `run_duration_seconds`
- `configured_customer_count`
- `alerted_customer_count`
- `ai_calls`
- `ai_fallbacks`
- `ai_fallback_rate`
