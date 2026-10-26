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

Verified after reconciliation: Argo, Wallabag and CrowdSec are all Synced/Healthy. Wallabag final deployment uses the hashed configuration reference. CrowdSec bouncer has 2/2 updated and available replicas on node-10 and node-50; the normal canary returns HTTPS 200.

**Production expansion blocked by a newly reproduced cold-start failure:** an isolated bouncer with the same pinned v0.8.2 image and API-key reference, but a LAPI Service with no endpoints, returned allow through its dedicated canary route. Repeated gateway requests returned 200 and the canary body, despite readiness NOT_SERVING and EndpointSlice ready=false/serving=false. The dedicated SecurityPolicy was Accepted with failOpen=false. The normal control also returned 200. This is an application-level allow response, not an Envoy transport failure; failOpen=false alone is insufficient. The inspected v0.8.2 decisions/cache.go GetDecision path returns no decision for an empty cache without checking syncComplete. Patch and test initialization/readiness enforcement in the authorization path before production attachment; inspect stale-cache behavior separately. Temporary test route, policy, deployment, services, ConfigMap and NetworkPolicy were deleted. No production routes were attached.

The isolated Argo render-test pod was deleted. Full patching remains in progress; the Wallabag runtime, Argo bundled Go helpers/KSOPS, application rebuild queue, lateral-movement controls, trust stores and stateful upgrades are not complete.

## CrowdSec authorization fix prepared

- Upstream PR: https://github.com/kdwils/envoy-proxy-crowdsec-bouncer/pull/286 (attached to this task). Fork: RobertDWhite/envoy-proxy-crowdsec-bouncer. Source checkout: /tmp/envoy-bouncer-security-20261026, branch codex/fail-closed-before-initial-sync. Code change 24d9211; current fork build revision 5abbc470d66628c9014bda25de9bdd6ab40e3f0b includes workflow-activation commits.
- GetDecision now returns an error until initial sync completes. The existing server mapping returns gRPC Unavailable, which works with fail-closed gateway configuration. Initialized empty caches still allow traffic. Explicit exemptions, disabled-bouncer mode and later stale-cache behavior are unchanged; this does not close the stale-cache gate.
- Regression failed before the fix and passes afterward. All unit tests with race detection, go vet and build pass using Go 1.26.9. An actual compiled server with LAPI unavailable at startup returned Unavailable for a gRPC authorization request. This local binary test does not replace the pending cluster/gateway test.
- Fork workflows initially did not schedule. Explicit repository Actions enablement followed by a push started the build. Main CI run 40269197090 is active and will publish a multi-platform image; branch CI run 40269195689 is also active. Do not restart these merely because polling times out. Local Docker daemon does not respond; use the confirmed CI handles for image builds.
- Production still runs the original pinned v0.8.2 bouncer on the canary only. Resolve the published digest, scan it, repeat the isolated cold-start and recovery checks, then perform ban/header/HA tests before switching the canary or attaching production routes.
- Added a real gRPC pre-sync authorization regression to the existing functional health suite, followed by its normal real-LAPI sync and readiness tests. Functional code compiles locally; full container-based execution is running as job 40269389102 at PR head 5d857dee56c9fb511a6d9a518e923b8d060d3616. Companion CI is 40269389081. The main image build 40269197090 remains active at the binary-equivalent 5abbc47 revision (the later commit changes only tests). Continue these handles rather than starting duplicate builds.

### Candidate results and next rebuild

- Build 40269197090 and functional suite 40269389102 both succeeded. Candidate index sha256:3bbc5399cac274c95a11e30405013b31350326ca4bd9122de4b7364eb25a54f1 (amd64/arm64) passed live isolated gateway tests: unavailable initial LAPI 403, recovered LAPI 200, unique test-pod ban 403, spoofed XFF/X-Real-IP/X-Envoy-External-Address while banned 403, decision removal 200. Temporary decision and test resources were removed. Normal control remained 200. The workstation path is NATed by the Tailscale subnet router; it does not prove individual WAN/tailnet client identity. The test pod's source address was separately matched in gateway logs before banning only that address.
- Manual candidate scans include unfixed findings. Raw JSON is saved privately under ~/Library/Application Support/whitehouse-security/candidate-scans-20261026/. Bouncer candidate: zero critical, one high, CVE-2026-111943 in Go 1.26.9. This is the compromised-intermediate trust fix: https://pkg.go.dev/vuln/GO-2026-6809. Fork commit 45d83ff updates Dockerfile and go.mod to 1.26.10; race tests and vet pass. New image build must finish and be scanned before deployment. It supersedes the otherwise validated 5abbc47 image.
- LAPI source inspection confirms stream progress is stored per bouncer/API key (StreamCursor; deletion deltas also use LastPull). Shared-key replicas are therefore not a valid HA configuration. Replace the two shared-key replicas with two single-replica deployments using separate encrypted credentials, common Service/PDB labels and required node separation. The secondary LAPI identity envoy-secondary was created; its key is SOPS-encrypted in 08-envoy-bouncer-secondary.sops.yaml and round-trip/admission validation passed. Verify both identities observe bans and deletions before production rollout. The isolated candidate previously shared the original identity; its tests do not establish independent per-replica delivery.

## Pages runtime candidate

- Source worktree /tmp/pages-security-20261026, RobertDWhite/pages-mcp PR https://github.com/RobertDWhite/pages-mcp/pull/3 (draft, attached). Commit 06f2506bd8573e3c306c2c1f44fe12bdb102a482 pins Python 3.14.9 base, installs OS updates and requires fixed Perl, PyJWT and certifi versions. Build 40269600884 succeeded; image index sha256:34987b824109d40d24a82a59c4cbf805a631b04d43172e3c1a615a04337e08d5.
- Isolated pod had no production credentials, data PVC or Git remote; default-deny network policy. Verified Python 3.14.9, Perl 5.40.1-6+deb13u1, PyJWT 2.15.1, certifi 2026.10.23; unauthenticated MCP 401, authenticated initialize/tool listing, temporary site creation/static serving/deletion passed. Production Pages and its content were untouched; temporary pod/policy removed.
- Full candidate scan: 1 critical/58 high/97 medium including unfixed findings. Targeted old Perl/PyJWT/certifi findings are gone; remaining critical is OpenSSH client CVE-2026-60002, no fixed trixie package. Debian rates this a minor/no-DSA issue; upstream fixes are in 10.4+, current release 10.6p1. Review/rebuild the SSH client or apply a verified targeted backport before calling this fully patched. Do not substitute an unstable Debian repository wholesale. Sources: https://security-tracker.debian.org/tracker/CVE-2026-60002 and https://www.openssh.org/releasenotes.html.
- Pages production still uses v0.2.4. Its pages-data PVC is Longhorn; take a fresh recovery snapshot before a verified candidate rollout. Git mirror/SSH compatibility still needs testing in addition to the isolated MCP test.

### Latest execution state

- Independent-identity bouncers deployed by cluster commit 740b255d (merged into main at 35067b55). Primary and secondary each have one Ready replica on different nodes, two ready Service endpoints, canary HTTPS 200, Argo Synced/Healthy. Both still use the old pinned v0.8.2 image pending the fully patched build. Per-identity ban/deletion delivery tests remain required.
- Go 1.26.10 builds initially failed because the existing CI runner sets GOTOOLCHAIN=local with Go 1.26.9. Retained the module's existing language/compatibility floor while keeping the explicit Docker builder at 1.26.10; this preserves the production toolchain fix. Current fork revision f0f4ad4d0c130aa68e25b162213e83f7f04b50de: main image build 40270570581, branch CI 40270570577 and functional suite 40270570523 are running. Inspect these exact handles. Scan the resulting image to prove its compiled Go version and cleared CVE-2026-111943 before deployment.
- Pages SSH currently uses accept-new with an ephemeral /tmp known_hosts file. Add a configured pinned known-hosts path for the actual GitHub mirror and verify SSH/Git read access; do not treat a hostname alone as endpoint authentication. Current Debian and stable Alpine packages retain the OpenSSH issue; assess a reviewed source build/backport rather than switching to an unstable distribution.

### Final bouncer candidate ready for rollout

- Main build 40270570581 and functional suite 40270570523 succeeded. Final image sha-f0f4ad4d0c130aa68e25b162213e83f7f04b50de, index sha256:50cc9b9c5380b57962c87ef9859bbdcf5a299f71e108048f8dfe1ac6308b02dd. Manual full scan (unfixed included) reports no vulnerabilities and confirms Go stdlib v1.26.10. Private report: candidate-scans-20261026/bouncer-final.json.
- Repeated live isolated cold-start test with its own temporary LAPI identity: Accepted gateway policy returns 403 while LAPI has no endpoints, then 200 after recovery. Temporary identity envoy-security-check-20261026 and Secret crowdsec-security-check require cleanup after final verification.
- Both primary and secondary desired images now use the central Kustomize version/digest mapping; exact rendered images and server admission dry run pass. This updates the existing canary bouncer, not production route attachments. Verify independent ban/deletion delivery and replica disruption after rollout.

## Final candidate verification and recovery rehearsal

- Pages source PR #3 merged at cf8ecf659c492b74b3fe5854f9cdb12f0936b480. Published candidate f9b2df06a320f21d44fe670d5e2166d64a517211 / sha256:7044b7defa69021c0f8f2c2344268d063bdcb2365d50a0c2a8d1b790bdf71a60 uses checksum-verified OpenSSH 10.6p1, Python 3.14.9 and updated dependencies; removes pip/ensurepip; supports pinned known hosts. Full private scan: zero critical, 49 high, 85 medium, none with listed fixes. Final-image isolated MCP authentication/tool/site lifecycle checks and pinned-host read-only Git access pass. Production remains v0.2.4 until restore gate completes.
- Snapshot pages-pre-security-20261026 ReadyToUse; cloned PVC pages-security-restore-check attached/healthy and read-only test pod Running. Aggregate full-content comparison against stable production content is actively reading storage; do not restart it or claim restore verification before completion. GitOps candidate image/digest, no API token and known-host configuration render correctly; server dry run passes.
- Both independently credentialed bouncers run the final Go 1.26.10 image. Per-identity ban/spoof/deletion tests passed previously. After canary readiness/retry settings in aba9678c, single-replica PDB eviction returned 10/10 HTTP 200 requests. A dedicated isolated route with identical readiness/retry settings returned 5/5 HTTP 500 responses when all authorization Service endpoints were removed; fail-closed behavior held, but the test assertion expected 403 and failed on status mismatch. Service selector was restored in finally; a separate request verified HTTP 200 recovery and the policy remained Accepted. Production expansion still requires bounded stale-cache semantics, WAN identity and alert delivery.
