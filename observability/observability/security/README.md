# Security SOC

Grafana dashboard: https://grafana.internal.white.fm/d/security-soc/security-soc

## Data paths

- Ubuntu RKE2 hosts: rsyslog -> TCP RFC5424 `10.99.5.111:1514` -> Promtail -> Loki. Each host has a 256 MB disk-assisted queue and a one-minute heartbeat. `configure-host.sh rke2-node-N` installs and validates forwarding; cloud-init also configures future nodes. TCP transport is LAN-only, without TLS. Do not expose this listener to the Internet.
- UniFi devices: RFC3164 UDP `10.99.5.111:1514` -> Promtail -> Loki (`source="unifi"`, `source_type="network"`). UDP uses the receive time because RFC3164 timestamps omit the year and timezone. Network events default to `category="network"`; sshd/dropbear/login/sudo/su app events use `authentication`. The SOC dashboard includes per-device received-event rates and original log lines. UDP delivery is best-effort; these panels are not a device-inventory completeness check. Controller forwarding uses `enabled=true`, `this_controller=false`, `ip=10.99.5.111`, `port=1514`. UniFi MAC/model header tags are normalized into `device_id` plus the process name extracted from the unchanged message body. `device_id` distinguishes devices with identical hostnames. On 2026-11-11, real events from 11 distinct switch/AP identities reached Loki after the controller change. This is observed coverage, not a claim that every model exports syslog. The installed UniFi MCP 0.23.1 has no syslog-setting tool; controller configuration was applied through its authenticated API.
- Kubernetes control planes: metadata-safe audit policy -> local rotated audit file -> Promtail -> Loki. `rke2-audit-config.yaml` documents the host drop-in. Install the policy first and restart server nodes sequentially. Secrets, TokenRequests and TokenReviews are metadata-only; no credential payloads are collected by this policy.
- Falco: syscall sensor -> Falcosidekick -> Loki (`source="syscall"`). Falcosidekick sends no direct Matrix webhook; Alertmanager is the single notification path. Raw events retain their original priority.
- Fleet: scheduled reports -> osquery TLS logger -> Fleet stdout -> CRI -> Promtail (`source_type="endpoint"`) -> Loki. Results use the authenticated Fleet enrollment path; no new endpoint-to-Loki access is needed.
- Loki rules -> Alertmanager `security` receiver -> Hookshot -> encrypted Matrix Security room. Loki remote-writes alert state to Prometheus for the SOC active-alert panels.

## Classification and cadence

Security alerts carry `security=true`, `severity`, `category`, and `source`. Alert categories are authentication, privilege, account_change, runtime, execution, credential_access, network and telemetry_health. Fleet event streams also distinguish persistence and endpoint_activity. ATT&CK identifiers are included only where the detection supports a specific technique; they are not proof of compromise.

Loki evaluates rules every 30 seconds. Critical alerts have no notification group wait; warnings wait 10 seconds. Subsequent updates group for 15 minutes. High-severity alerts repeat every four hours; other security alerts repeat every 12 hours. Event-only alerts do not send resolved notifications: a lookback window expiring is not incident resolution. Telemetry-health alerts still send recovery notifications. Runtime critical events use a 30-minute rolling window; Kubernetes exec/attach uses one hour. Neither delays the first detection. Warning burst thresholds remain unchanged.

Fleet reports cover enrolled Linux, macOS and Windows teams. Heartbeats run every 60 seconds, listening-port diffs every 120 seconds, account and persistence changes every 300 seconds. Snapshot queries for unexpected UID 0 accounts run every 60 seconds on Linux. Initial differential results establish a baseline and are not automatically treated as incidents. Queries omit process command lines and password hashes. Collection is scheduled state inspection, not full EDR process-event capture. Sleeping/offline endpoints cannot report. Apple mobile/TV/watch MDM platforms do not run osquery and are not claimed as runtime telemetry coverage. Unassigned hosts are not silently moved to a managed team.

## Investigation

1. Start with source health. Missing telemetry is not a clean bill of health. The dashboard lists observed hosts and endpoints, while explicit missing-heartbeat rules cover every expected RKE2 node all three audit sources, and the Fleet agents expected on each RKE2 host.
2. Open the alert's category, source host and time window in Explore. Examine original log lines and identities before attributing malicious intent.
3. Match privilege changes and interactive access against authorized maintenance. Allowlist only specific known activity; retain source-health alerts.
4. Preserve relevant evidence before containment or credential rotation. The current Loki retention is 30 days on local filesystem storage; it is not immutable evidence storage.

## Validation and recovery

- Emit `logger -t siem-heartbeat 'SIEM_HEARTBEAT host=rke2-node-N'` on a host; confirm its canonical host label in Loki.
- Controlled SSH detection test: eight `logger -p authpriv.warning -t sshd 'Failed password for invalid user soc-validation from 192.0.2.10 port 2222 ssh2 [SOC TEST]'` messages. This tests parsing and detection without attempting authentication. Confirm the alert reaches Matrix, then resolves after the window expires.
- Query `{job="kube-audit"}` and check all three server node labels. Never dump audit contents into a public PR.
- Query `{source_type="endpoint"} |= "soc_heartbeat"` and verify actual enrolled endpoint identities, not just Fleet pod health.
- Node 12 forwarding is queued in `node-12-syslog-job.yaml`; it can execute only when that host returns. Keep the source-gap alert enabled until a heartbeat arrives.
- Promtail positions persist at `/var/lib/promtail/positions.yaml`. Preserve this file when restarting collectors. Config revision annotations trigger collector/Alertmanager rollouts. Loki local rule files are polled by the ruler and Grafana provisions mounted dashboards periodically; avoid restarting Loki for a rule-only change.
- Matrix hook transform source: `apps/social/matrix-stack/hookshot/transforms/security-soc.js`. The live transform is a Hookshot room state event. Editing its source alone does not update room state. The webhook URL stays in a SOPS Secret.

Rollback: revert the GitOps change; remove `/etc/rsyslog.d/60-security-forward.conf` and `/etc/cron.d/security-log-heartbeat` on hosts and restart rsyslog if forwarding must stop. To disable API auditing, remove only the SOC drop-in and roll server restarts sequentially. Preserve historical logs and collector offsets.

## Expected runtime activity

`runtime-policy.json` is the reviewed registry: exact rule, workload/container,
image, executable, parent, and where applicable file or runtime-hook ancestry.
Every entry has an owner, evidence and an expiry. The initial entries expire on
2026-12-11 UTC. Expiry is enforced against the event timestamp inside Loki: new
events stop matching automatically, while historical evidence retains its
classification. Never extend an entry without reviewing the current workload.
The database probe exception is now query-time and workload-scoped, replacing
the old global `pg_isready` source suppression.

`runtime_policy.py` generates identical classifiers for the Loki rules and SOC
panels. After editing the registry run it, then run the tests. Do not hand-edit
the generated expressions. Parsing is anchored to Falco's field prefix/suffix;
command arguments are not used to establish an expected workload. Missing or
changed fields fail open to review/alerting, never to expected. The original
log lines and severities remain in Loki for 30 days, including expected activity.

The dashboard distinguishes Warning+ **expected**, **actionable** (unmatched
Error or higher), and **unreviewed** (unmatched Warning) events. These are triage
classes, not declarations of compromise. The raw-priority panel still includes
Notice events. Expected evidence has its own log panel and `expected_reason`.
Unknown warnings retain the existing burst detection. Unknown critical events
still notify immediately. No namespace, administrator, or entire Falco rule is
blanket trusted. The observed database dump/restore and Longhorn tee events have
not been classified as expected without additional maintenance evidence.

Validation:

```sh
python3 observability/observability/security/runtime_policy.py
python3 -m unittest discover -s observability/observability/security/tests
python3 observability/observability/security/tests/verify_loki_policy.py http://10.99.5.114:3100
```

The live check writes synthetic records with `source=soc-policy-test`, outside
production syscall selectors, and verifies parsing, scope boundaries, command
spoofing, malformed records and expiry against Loki's actual template engine.

## Temporary maintenance

Keep operator exec/attach audited. Create a silence only for the authorized
operation, with an exact identity AND namespace; runtime silences require an
exact host AND rule. The helper refuses source-health silences and durations
over four hours. It prints a reviewable payload by default; `--apply` creates it.
All silences retain the owner/reason and expire automatically. Alertmanager stores
silences and notification deduplication state on its persistent volume, so a pod
replacement does not reset them. They affect
notifications only; active detections and original evidence remain visible.

```sh
kubectl -n observability port-forward svc/alertmanager 19093:9093
python3 observability/observability/security/maintenance.py \
  --alert SecurityKubeExec --scope user=system:admin --scope namespace=observability \
  --minutes 60 --owner Robert --reason 'Approved SOC maintenance' --apply
```

The response contains the silence ID. End maintenance early with
`curl -X DELETE http://127.0.0.1:19093/api/v2/silence/<silenceID>`.
Do not silence all `security=true` alerts or permanently exclude `system:admin`.
