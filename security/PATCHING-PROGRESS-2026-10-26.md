# Full patching execution — October 26, 2026

Goal remains full execution and verification of PATCH-PLAN-2026-10-25.md, not merely an Argo upgrade.

## Argo CD deployed; residual verification and dependency fixes remain

- Clean managed worktree based on origin/main (750bf38b); unrelated original checkout changes preserved.
- Private namespace resources, credentials, Applications/AppProjects/ApplicationSets and Argo CRDs exported to owner-only files under `~/Library/Application Support/whitehouse-security/argocd-pre-v356-20261026/`. JSON validity checked. This is a recovery export, not a full disaster-recovery rehearsal.
- Reviewed 3.2→3.3, 3.3→3.4 and 3.4→3.5 upstream upgrade notes. No source-hydrator apps or plain-HTTP application source URLs observed; impersonation parameters absent. Helm OCI dependency behavior still needs candidate rendering checks.
- Existing chart 10.11.1 already targets v3.5.6; old global image override prevented upgrade. Candidate explicitly pins v3.5.6 plus verified multi-platform index digest. Proxy Chart appVersion metadata corrected.
- Self-managed Argo Application gains ServerSideApply=true, required by newer ApplicationSet CRDs.
- Isolated render-test pod uses candidate image, existing KSOPS initializer and read-only age-key mount; no service-account token or listening Argo server. Delete after validation.

## Remaining batches

- Verify/deploy Argo candidate, functional auth/render/sync and fresh vulnerability results.
- Wallabag supported runtime/mitigations and tested migration.
- Condo, Pages, MISP modules, OpenClaw/Congress and other vulnerable application rebuilds.
- Production CrowdSec rollout with HA, source and outage tests.
- Network isolation, service exposure, admission and RBAC restrictions with dependency tests.
- Effective trust-store inventory and remediation, remaining main/init image evidence, unfixed vulnerability visibility.
- Stateful/storage updates with restore validation; offline node review.

Do not mark the overall goal complete while any batch remains unverified.

Candidate validation: image reports Argo v3.5.6, Helm v4.2.1, Kustomize v5.8.1. Existing chart 10.11.1 upstream appVersion is v3.5.6. Chart render produced all five Argo workloads with the pinned digest. MISP/FreshRSS KSOPS and plain Wallabag builds passed; decrypted output was discarded. Full chart server-side dry-run passed. Mac tar metadata was removed from test files before rendering. No production mutation occurred during rendering tests.

Production: commit 5c4657d6 synchronized successfully; all five Argo workloads now run the pinned v3.5.6 digest with desired replicas ready. Argo, FreshRSS, MISP and Wallabag are Synced/Healthy after hard refresh at 68d9a008. TLS-verified gateway requests return HTTP 200 and /api/version reports v3.5.6. Interactive SSO/admin login and the 30-minute observation gate remain unverified.

Fresh candidate Trivy report: 2 critical/120 high/122 medium, with the targeted Argo application criticals removed. Remaining critical occurrences are CVE-2025-68121 in bundled Go stdlib v1.25.3 and v1.24.0; identify and rebuild affected helper binaries. KSOPS v4.5.1 (latest upstream release checked) separately has three critical occurrences in Go v1.25.0. This is not a vulnerability-free image.

## Wallabag interim containment

- Live PHP CLI and FPM both confirmed PHP 8.1.32 with session.upload_progress.enabled=On. Upstream PR https://github.com/wallabag/docker/pull/484 remains open; all four database CI checks passed. A supported production runtime is still required.
- Created a consistent SQLite backup using VACUUM INTO and copied it to owner-only private storage under ~/Library/Application Support/whitehouse-security/wallabag-pre-mitigation-20261026/. Offline integrity_check returned ok. This is not a full application restore rehearsal. Data PVC is local-path, so a Longhorn VolumeSnapshot would not back it up.
- Startup-level PHP INI disables upload progress; Kustomize-generated hashed ConfigMap forces worker replacement on changes. Disable unused Kubernetes service-account token mount. Pin current 2.6.14 multi-platform index digest for reproducible rollback. These changes mitigate exposure; they do not patch the old runtime.
- Reference: https://www.php.net/archive/2026.php#2026-10-23-1. Global startup configuration is required; runtime ini_set or .user.ini must not be relied on.
- Rollback: revert only this deployment/config change and reconcile; no schema or data changes are intended. Rollback re-enables the vulnerable feature, so prefer correcting the mounted configuration while preserving the mitigation.
- Initial rollout exposed a generated-ConfigMap namespace reference mismatch. Added namespace: wallabag and verified rendered reference equals generated name. Correction pushed after merging concurrent unrelated main changes. Live FPM then reported Off/Off, the service-account token was absent, and database quick_check returned ok. Gateway HTTPS returned 302 with explicit DNS resolution to 10.99.5.110; this client does not resolve the Wallabag internal name normally. Recheck final revision/replica health after reconciliation.

## CrowdSec availability prerequisite

- Bouncer moves from one replica to two on distinct nodes, with zero-unavailable rolling updates and minAvailable: 1 disruption budget. Admission dry run passes. The existing canary returned HTTPS 200 before this change (explicit gateway DNS resolution).
- This does not attach production routes. Cold-start LAPI failure, rolling availability and per-replica deny behavior must pass before expansion. WAN identity and end-to-end alert delivery remain unverified.
- Rollback: revert this availability-only change; existing SecurityPolicy remains intact.
