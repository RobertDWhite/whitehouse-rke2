# Persistent Trivy result archive

This separate, read-only Kubernetes client retains completed scan-container output before Trivy's one-hour job TTL expires. It does not change Trivy's CRD persistence backend or remove findings from reports. The only writable storage is the `trivy-archive` 10 GiB Longhorn PVC; Argo pruning is disabled for that PVC.

Full results can contain secret findings. There is no report-serving HTTP endpoint: port 9100 serves aggregate metrics and a health endpoint only. RBAC permits listing pods and vulnerability reports, and reading scan jobs/logs only in `trivy-system`. It cannot read Kubernetes Secrets, execute commands, or delete jobs. NetworkPolicy permits API access, DNS, and metrics scraping from observability. The API endpoint IPs in `resources.yaml` must be updated if control-plane addresses change.

Every 30 seconds the collector archives completed vulnerability scan jobs. Files use job UID directories and pod UID/container names, preventing namespace/name reuse collisions. Raw output is gzip-wrapped, checksummed, and written before an atomic JSON receipt. Trivy's original base64/bzip2 result is recoverable from the raw file, including details too large for a Kubernetes report. Decode errors leave raw output retained and raise the archive-error metric. API responses and decompressed results have explicit memory bounds; over-limit results fail visibly. Files are 0600 and directories 0700 under the non-root process identity.

Retention is 14 days since the most recent file write in a job directory. Low disk space alerts at 15 percent free. The PVC protects against pod replacement; disaster recovery of the archive and a separate off-cluster backup have not been tested. The collector has a single replica and relies on the one-hour retained-job window to recover from a short outage; it cannot guarantee recovery after an outage longer than that window.

Coverage refreshes every five minutes and compares immutable digests with both CRD reports and retained receipts. Main, init, and ephemeral containers in Running pods are separate denominators. Missing image IDs, unmatched digests, and matched results older than 48 hours are separately reported. Index/platform digest differences remain possible: unmatched means missing exact-digest evidence, not proof that no scan occurred. Existing scanner filters still omit unfixed/low findings.

`trivy_archive_latest_findings` sums severity occurrences from the latest archived receipt per digest tuple across the retention window. It is not limited to running images and is not a unique-CVE count. Do not add it to CRD finding counts: the sources overlap. Detailed CVE IDs remain available in CRDs/full reports; the operator's optional per-CVE Prometheus series is disabled because duplicate labels caused its entire metrics endpoint to return 500.

Validation:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s security/trivy-archive
kubectl kustomize security/trivy-archive | kubectl apply --dry-run=server -f -
kubectl -n observability exec deployment/prometheus -- \
  wget -qO- http://trivy-archive.trivy-system.svc.cluster.local:9100/metrics
```

Four unit tests cover full-result recovery, atomic receipts and file permissions, malformed-result retention, empty reports, and init/ephemeral inventory. Live validation checked restart persistence, checksum integrity of nine retained reports, metrics scraping, and loaded alert-rule health. Four historical results were imported from the private pre-cleanup archive; their receipts explicitly identify recovery provenance and use Trivy's CreatedAt timestamp.

To recover a report, copy it into an owner-only local directory without printing its contents, decompress the outer gzip, base64-decode, then bzip2-decompress. Check the SHA-256 of the original scan log against the receipt first. Do not attach raw output to GitHub or put it in Git.
