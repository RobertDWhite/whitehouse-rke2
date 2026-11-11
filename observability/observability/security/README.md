# Security SOC

Grafana dashboard: https://grafana.internal.white.fm/d/security-soc/security-soc

## Data paths

- Ubuntu RKE2 hosts: rsyslog -> TCP RFC5424 `10.99.5.111:1514` -> Promtail -> Loki. Each host has a 256 MB disk-assisted queue and a one-minute heartbeat. `configure-host.sh rke2-node-N` installs and validates forwarding; cloud-init also configures future nodes. TCP transport is LAN-only, without TLS. Do not expose this listener to the Internet.
- Kubernetes control planes: metadata-safe audit policy -> local rotated audit file -> Promtail -> Loki. `rke2-audit-config.yaml` documents the host drop-in. Install the policy first and restart server nodes sequentially. Secrets, TokenRequests and TokenReviews are metadata-only; no credential payloads are collected by this policy.
- Falco: syscall sensor -> Falcosidekick -> Loki (`source="syscall"`). Existing direct Falco error notifications remain available, so critical runtime events may also appear on that path.
- Fleet: scheduled reports -> osquery TLS logger -> Fleet stdout -> CRI -> Promtail (`source_type="endpoint"`) -> Loki. Results use the authenticated Fleet enrollment path; no new endpoint-to-Loki access is needed.
- Loki rules -> Alertmanager `security` receiver -> Hookshot -> encrypted Matrix Security room. Loki remote-writes alert state to Prometheus for the SOC active-alert panels.

## Classification and cadence

Security alerts carry `security=true`, `severity`, `category`, and `source`. Categories are authentication, privilege, account_change, runtime, execution, credential_access and telemetry_health. ATT&CK identifiers are included only where the detection supports a specific technique; they are not proof of compromise.

Loki evaluates rules every 30 seconds. Critical alerts have no notification group wait; warnings wait 10 seconds and group subsequent updates for five minutes. Resolutions are sent. Rules with sustained thresholds deliberately take longer. High-severity alerts repeat hourly; other security alerts repeat every four hours.

Fleet reports cover enrolled Linux, macOS and Windows teams. Heartbeats run every 60 seconds, listening-port diffs every 120 seconds, account and persistence changes every 300 seconds. Snapshot queries for unexpected UID 0 accounts run every 60 seconds on Linux. Initial differential results establish a baseline and are not automatically treated as incidents. Queries omit process command lines and password hashes. Collection is scheduled state inspection, not full EDR process-event capture. Sleeping/offline endpoints cannot report. Apple mobile/TV/watch MDM platforms do not run osquery and are not claimed as runtime telemetry coverage. Unassigned hosts are not silently moved to a managed team.

## Investigation

1. Start with source health. Missing telemetry is not a clean bill of health. The dashboard lists observed hosts and endpoints, while explicit missing-heartbeat rules cover every expected RKE2 node and all three audit sources.
2. Open the alert's category, source host and time window in Explore. Examine original log lines and identities before attributing malicious intent.
3. Match privilege changes and interactive access against authorized maintenance. Allowlist only specific known activity; retain source-health alerts.
4. Preserve relevant evidence before containment or credential rotation. The current Loki retention is 30 days on local filesystem storage; it is not immutable evidence storage.

## Validation and recovery

- Emit `logger -t siem-heartbeat 'SIEM_HEARTBEAT host=rke2-node-N'` on a host; confirm its canonical host label in Loki.
- Controlled SSH detection test: eight `logger -p authpriv.warning -t sshd 'Failed password for invalid user soc-validation from 192.0.2.10 port 2222 ssh2 [SOC TEST]'` messages. This tests parsing and detection without attempting authentication. Confirm the alert reaches Matrix, then resolves after the window expires.
- Query `{job="kube-audit"}` and check all three server node labels. Never dump audit contents into a public PR.
- Query `{source_type="endpoint"} |= "soc_heartbeat"` and verify actual enrolled endpoint identities, not just Fleet pod health.
- Node 12 forwarding is queued in `node-12-syslog-job.yaml`; it can execute only when that host returns. Keep the source-gap alert enabled until a heartbeat arrives.
- Promtail positions persist at `/var/lib/promtail/positions.yaml`. Preserve this file when restarting collectors. Config revision annotations trigger rollouts; update their values when changing the mounted configuration.
- Matrix hook transform source: `apps/social/matrix-stack/hookshot/transforms/security-soc.js`. The live transform is a Hookshot room state event. Editing its source alone does not update room state. The webhook URL stays in a SOPS Secret.

Rollback: revert the GitOps change; remove `/etc/rsyslog.d/60-security-forward.conf` and `/etc/cron.d/security-log-heartbeat` on hosts and restart rsyslog if forwarding must stop. To disable API auditing, remove only the SOC drop-in and roll server restarts sequentially. Preserve historical logs and collector offsets.
