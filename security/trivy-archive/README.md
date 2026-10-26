# Persistent Trivy result archive

This separate, read-only Kubernetes client retains completed scan-container output before Trivy's two-minute job TTL expires. It does not change Trivy's CRD persistence backend or remove findings from reports. The only writable storage is the `trivy-archive` 10 GiB Longhorn PVC; Argo pruning is disabled for that PVC.

Full results can contain secret findings. There is no report-serving HTTP endpoint: port 9100 serves aggregate metrics and a health endpoint only. RBAC permits listing pods and vulnerability reports, and reading scan jobs/logs only in `trivy-system`. It cannot read Kubernetes Secrets, execute commands, or delete jobs. NetworkPolicy permits API access, DNS, and metrics scraping from observability. The API endpoint IPs in `resources.yaml` must be updated if control-plane addresses change.

Every 30 seconds the collector archives completed vulnerability scan jobs. Files use job UID directories and pod UID/container names, preventing namespace/name reuse collisions. Raw output is gzip-wrapped, checksummed, and written before an atomic JSON receipt. Trivy's original base64/bzip2 result is recoverable from the raw file, including details too large for a Kubernetes report. Decode errors leave raw output retained and raise the archive-error metric. API responses and decompressed results have explicit memory bounds; over-limit results fail visibly. Files are 0600 and directories 0700 under the non-root process identity.

Retention is 14 days since the most recent file write in a job directory. Low disk space alerts at 15 percent free. The PVC protects against pod replacement; disaster recovery of the archive and a separate off-cluster backup have not been tested. The collector has a single replica and relies on the two-minute retained-job window (four 30-second polls) to recover from a short outage; it cannot guarantee recovery after an outage longer than that window. The window is deliberately short because Trivy counts retained completed jobs against its five-job concurrency limit; a one-hour TTL capped scanning at about five images per hour while 24-hour report expiry removed results faster than they were replaced.

Coverage refreshes every five minutes and compares immutable digests with both CRD reports and retained receipts. Main, init, and ephemeral containers in Running pods are separate denominators. Missing image IDs, unmatched digests, and matched results older than 48 hours are separately reported. Index/platform digest differences remain possible: unmatched means missing exact-digest evidence, not proof that no scan occurred. The scanner configuration now includes all severities and unfixed findings. Older retained receipts can still reflect previous filtering; check scan time and configuration before interpreting coverage.

`trivy_archive_latest_findings` sums severity occurrences from the latest archived receipt per digest tuple across the retention window. It is not limited to running images and is not a unique-CVE count. Do not add it to CRD finding counts: the sources overlap. Detailed CVE IDs remain available in CRDs/full reports; the operator's optional per-CVE Prometheus series is disabled because duplicate labels caused its entire metrics endpoint to return 500.

Validation:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s security/trivy-archive
kubectl kustomize security/trivy-archive | kubectl apply --dry-run=server -f -
kubectl -n observability exec deployment/prometheus -- \
  wget -qO- http://trivy-archive.trivy-system.svc.cluster.local:9100/metrics
```

Unit tests cover full-result recovery, atomic receipts and file permissions, malformed-result retention, empty reports, and init/ephemeral inventory. Live validation checked restart persistence, checksum integrity of nine retained reports, metrics scraping, and loaded alert-rule health. Four historical results were imported from the private pre-cleanup archive; their receipts explicitly identify recovery provenance and use Trivy's CreatedAt timestamp.

To recover a report, copy it into an owner-only local directory without printing its contents, decompress the outer gzip, base64-decode, then bzip2-decompress. Check the SHA-256 of the original scan log against the receipt first. Do not attach raw output to GitHub or put it in Git.

CRD ingestion is tracked separately: `trivy_inventory_crd_unmatched_digests` and `trivy_inventory_crd_stale_digests` do not count archived scans as stored CRDs. `trivy_inventory_fresh_archive_without_fresh_crd` counts running digests with a fresh archive receipt but no equally fresh CRD evidence (48-hour threshold). `TrivyReportIngestionGap` alerts after30 minutes. Counts are per main/init/ephemeral set, so summing kinds is not a unique-image count. This instrumentation does not solve oversized writes, confirm platform equivalence, or establish complete unfiltered scans from older receipts.

## Partitioned vulnerability reports

The archive coverage reader accepts ordinary reports and complete `sha256-tree-v1` partition manifests. Parts alone never satisfy coverage. It validates referenced namespace/name/order, root and generation, scan headers, summaries and the reconstructed content checksum. Missing, mixed, unknown-protocol or corrupt sets increment `trivy_archive_crd_incomplete_sets` and are excluded from CRD coverage. Independent full scan receipts remain visible as archive evidence.

The checksum protocol and writer are in the operator fork's `docs/security/report-partitioning.md`. `testdata/partition-go.json` is synthetic output from `TestExportPartitionInteropFixture`, generated with `TRIVY_PARTITION_INTEROP_FIXTURE`; no private findings are included. Tests cover cross-language integrity, actual API null-field pruning, tampering, missing parts and metrics. Unknown future fields/formats fail conservatively rather than falsely proving completeness.

Integration remains staged: do not deploy the partition operator until incomplete-set alerts, old-part reclamation, TTL/retry behavior and whole-cluster memory/scrape tests pass. A local 19398-finding full-report reconstruction passed at approximately381MB peak RSS; that is not a cluster-wide capacity result.

The incomplete-set alert tests in `testdata/partition-alerts.yaml` expect the rendered `cluster-alerts.yml` rules at `/tmp/trivy-partition-rules.yaml`; run them with the deployed Prometheus version's `promtool test rules`. They exercise either producer, combined producers, timing and quiet zero/absent cases. The production API supplies an iterator; reader regression tests must retain iterator inputs to catch exhaustion.
