# Deployment verification — 2026-11-11

Verified against live services during the initial rollout:

- Six reachable RKE2 Ubuntu hosts (10, 11, 13, 14, 15, 50) forwarded syslog and recurring heartbeats to Loki.
- All three control planes (10, 11, 14) wrote audit events after sequential server restarts; Loki queries returned each canonical node label. Audit timestamps preserve the source event time.
- Missing Fleet agents were installed on rebuilt nodes 11, 13, 14 and 15. Their new enrollments were observed reporting to Fleet. Historical inventory records were preserved.
- Fleet report output reached Loki from Linux, macOS and Windows; 12 distinct endpoint identities had reported during verification. This is an observation, not a claim that offline or sleeping devices are covered.
- Grafana returned the provisioned Security SOC dashboard with 14 panels. All ten Loki-backed panel queries executed successfully. The active-alert Prometheus query contained Loki detections delivered by ruler remote write.
- Distinct, clearly marked synthetic SSH failure log messages triggered SecuritySSHBruteForce for node 10 and reached Alertmanager's security receiver. No actual authentication attempts were made.
- Alertmanager webhook delivery counters reported zero failures. The Security room contained new webhook events encrypted with m.megolm.v1.aes-sha2. Device-side display/decryption was not tested.
- Loki, Promtail and Alertmanager configuration validation and Prometheus rule validation passed. Fleet GitOps completed successfully. Runtime startup checks and live data queries were used to resolve issues not detected by syntax-only checks.

Node 12 was NotReady and unreachable over SSH during verification. Its host-forwarding Job remains queued and its missing-source alerts remain enabled. The observability application's overall health remains Progressing while this Job waits. All six online collectors are running. This queued rollout does not constitute verified ingestion from node 12.

Fleet collection is scheduled telemetry (60–300 seconds), not full EDR coverage. iOS/iPadOS/watchOS/tvOS MDM enrollment does not provide osquery runtime events. The existing Grafana connector's credential returned 401; dashboard validation used the existing in-cluster administrator credentials without exposing them.
