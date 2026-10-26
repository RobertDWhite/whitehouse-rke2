# Cluster patch and hardening plan — October 25, 2026

## Decision

Execute small, independently reversible batches. First patch Argo CD, then address unpatched PHP and sensitive application images; expand the tested CrowdSec pilot and network/admission controls alongside that work. Do not wait for perfect scanner coverage before patching the confirmed Argo CD exposure. This document proposes changes; this assessment did not deploy them.

Baseline repository HEAD: `3569d461`. An existing uncommitted change in `apps/home/ha-screen/deployment.yaml` belongs to other work and must be preserved. Earlier October 23 handover statements about missing archives and missing CrowdSec integration are superseded by `IMPLEMENTATION-2026-10-24.md` and live checks below.

## Refreshed evidence

- 474 running pods, 259 distinct main-container image digests, 837 VulnerabilityReports. 211 digests matched CRDs (81.5%); 116 matched images had critical package findings. These are package occurrences, not unique exploitable vulnerabilities. Counts rose partly because visibility improved, not necessarily because risk increased.
- The archive collector additionally matched 217/259 main digests and 29/37 init digests. It reported 42 main and eight init digests unmatched, zero stale matched digests, zero missing image IDs, zero archive errors, 2,323 receipts, and approximately 9.3 GiB free. This later sample differs from the CRD-only snapshot. Do not add archive totals to CRD totals: they overlap. Collector coverage is exact-digest evidence, not proof every platform variant has been scanned.
- Falco Ready 6/6; node-12 remains NotReady. FreshRSS, MISP, Wallabag, CrowdSec and Trivy Argo applications were Synced/Healthy. Gateway routes remain OutOfSync/Healthy. MISP backend TLS verification remains enabled.
- CrowdSec is now v1.8.1: current matched report has zero critical and 76 high findings. Its bouncer has zero critical and 13 high findings. Neither is vulnerability-free. The bouncer deployment is Ready; only the canary SecurityPolicy uses extAuth. October 24 recorded real-IP/spoofing/failure tests; this pass did not repeat those tests or independently validate WAN behavior.
- 31 active namespaces have no NetworkPolicy. The five general Kyverno policies remain Audit/Ignore; only PHP host-access containment is Enforce/Fail.
- `security/posture-2026-10-25.json` contains selected evidence, not raw reports or secrets. Dates in scanner timestamps are UTC; the document date uses America/New_York.

## Newly prioritized findings

| Priority | Observed workload | Evidence and proposed treatment |
|---|---|---|
| P0 | Argo CD v3.2.3 | 7 critical/148 high findings; explicit old image pin overrides chart updates. Applicable upstream Kustomize remote-ref RCE and PostDelete project-check bypass. Repo-server mounts SOPS age key. Patch before broad app changes. |
| P0 | Wallabag 2.6.14 | 64 critical/247 high; PHP 8.1.32 packages in matched report. Existing isolation reduces exposure but does not fix runtime. PR #484 remains open/unmerged. |
| P1 | Condo v5.23.0 | 37 critical/614 high; public and internal routes; vulnerable ImageMagick packages are among findings. Rebuild from supported patched base and review file-processing paths. |
| P1 | MISP modules v3.0.6 | 21 critical/193 high, distinct from already-upgraded core. Upgrade/rebuild modules, test representative enrichment. |
| P1 | Pages MCP v0.2.4 | 14 critical/85 high; outdated Perl and system libraries in report. Rebuild source-owned image and remove unnecessary build/runtime packages. |
| P1 | OpenClaw custom image 2026.6.8-claude.1 | 21 critical/145 high. Rebuild dependencies; review privileged agent access and tool credentials. |
| P1 | CompreFace | Oversized archived report previously recorded 127 critical/1,647 high; current image remains outside CRD match. Match archived receipt to current digest before using its counts. Keep biometric service private and restricted; evaluate supported replacement if no maintainable image exists. |
| P2 | FlareSolverr, Kavita, Homebridge | Respectively 28/899, 24/372 and 24/363 critical/high findings. Browser/parser and host/LAN integration warrant dependency-specific upgrades and containment. |
| Separate maintenance | Immich Postgres, OpenCTI RabbitMQ/OpenSearch, Longhorn | Critical findings in stateful/infrastructure images. Require supported migration paths and tested restore; do not substitute arbitrary major versions. |

The image/package associations above come from live reports matched to running digests. Detailed exploitability review is still required. Repository-wide report samples can include old revisions; do not use those to select deployment versions without digest matching.

## Batch 0 — recovery and reproducible evidence

**Before any rollout:** fetch latest main; isolate unrelated working changes; record old rendered manifests/image digests; confirm out-of-band kubectl access independent of Argo. Capture application-specific backup and restoration instructions. Store credentials/exports in owner-only encrypted or approved private storage, never Git.

Run `python3 security/tools/posture_snapshot.py` for a fresh baseline and collect archive coverage metrics. Preserve existing CRD reporting and full-output archive. Run archive unit tests when changing it. Verify new alert rules are loaded and establish one end-to-end notification test.

**Exit:** rollback artifacts accessible, current versions recorded, backup restoration validated for the batch, and a known healthy application test exists. A ReadyToUse snapshot alone is not a database restore test.

## Batch 1 — Argo CD security upgrade (first maintenance window)

**Files:** `argocd/values/argocd-values.yaml`, `argocd/Chart.yaml`, `argocd/Chart.lock`, `argocd/kustomization.yaml`.

**Confirmed cause:** `global.image.tag: v3.2.3` still controls live repo-server despite chart 10.11.1. Live Kustomize options are `--enable-alpha-plugins --enable-exec`. Do not remove these blindly: KSOPS depends on plugin execution.

**Candidate target:** Argo CD **v3.5.6**, verified as the latest upstream stable release during this review. Resolve and verify its immutable image digest, scan it, check chart compatibility, and review 3.2→3.3→3.4→3.5 upgrade notes. Use staged minor transitions if required by those notes. This is a selected candidate, not a locally tested deployment bundle. If a reduced-jump option is needed, 3.3.15 is the advisory's minimum fixed 3.3 release; recheck support/current patch levels before choosing it.

Upstream confirms CVE-2026-113708 (Kustomize remote ref) affects this version range. It can execute in repo-server from repository content or server-side local manifest generation with Application get permission. Network egress denial alone cannot stop its local-file path. CVE-2026-77459 affects PostDelete hooks in 3.2; PreDelete was added later. These are applicable version/configuration findings, not an exploitation claim. The separate CVE-2026-113709 Helm-config-home flaw requires `--enable-helm`, which is NOT in the observed global options; do not label that specific vector confirmed here.

1. Back up Argo configuration, Applications/AppProjects and required credentials privately; retain a known-good bootstrap/recovery path.
2. Render candidate chart and all custom values. Verify image pin actually changes every Argo component. Check CRDs/RBAC, Dex/Redis compatibility, KSOPS init image and age-key mounts.
3. Test representative Helm, plain Kustomize and SOPS/KSOPS applications against candidate repo-server without production writes. Verify secret decryption succeeds without logging values. Do not run exploit payloads against production.
4. Deploy during an attended window. Verify admin/SSO access, Git fetch, manifest generation, controller reconciliation, and one low-impact application sync. Compare against pre-existing drift instead of accepting new unexplained errors.
5. Tighten direct argocd-server ingress (currently ingress `[{}]`) to actual gateway/admin/monitoring sources in a separate tested policy change. Review broad api-bot admin rights without silently revoking intended access.

**Pass:** desired image/digest everywhere, no new rendering or auth failures, benign test application reconciles, scan and vendor-version checks clear targeted application advisories. Monitor for 30 minutes.

**Rollback:** revert Git desired state and use the tested recovery manifests if Argo cannot reconcile itself. Restore config/CRDs only as required by the upgrade guide; preserve current state first. Returning to the old version reintroduces vulnerabilities, so retain access restrictions and schedule immediate correction.

## Batch 2 — Wallabag and sensitive application rebuilds

**Wallabag files:** `apps/misc/wallabag/`. Keep existing ingress/egress restrictions. Audit active FPM settings, including session upload progress, and apply the current PHP advisory's compatible mitigation while a supported build is prepared. Do not assume the upstream PR protects the running image.

- Choose a published supported fixed runtime only after inspecting the image, or build a maintained fork in CI from reviewed source. The scanner's old-branch package floor (php81 8.1.34-r0 for specific CVEs) is not evidence that all current PHP advisories are fixed or that PHP 8.1 is supportable.
- Clone the SQLite/data PVC from a fresh backup and test login, article import, extraction, export and migrations without modifying live data. Pin release plus digest in Kustomize only after tests and scan pass.
- If no compatible maintained runtime is available, keep isolation and propose temporary shutdown or replacement with a tested export/import path; do not leave an indefinite mitigation without an owner/deadline.

**Custom rebuild queue:** Condo → Pages MCP → OpenClaw/Congress, with MISP modules in parallel as a separate release. Deployment paths: `apps/home/condo/`, `apps/ai/pages/`, `apps/misc/misp/`. Find each source repository/CI owner before editing builds. Pages content owned by Brain must remain in its source repository; do not deploy direct edits over synced content.

- Rebuild with supported base images and updated OS/application dependencies. Remove compilers/dev headers and unused interpreters from final images where runtime tests permit.
- Reported package floors to verify against vendor repositories: Condo ImageMagick `8:6.9.11.60+dfsg-1.6+deb12u14`; Pages Perl `5.40.1-6+deb13u1`. These address identified package advisories, not the whole image. MISP modules requires a compatible release/rebuild, not merely a core upgrade.
- Produce new immutable release tags and digests. Test Condo auth/upload/worker jobs, Pages authenticated MCP and static serving, MISP module listing and representative enrichment, and agent tool permissions. Never print test credentials.

**Pass:** fresh candidate scan, targeted findings removed or explicitly investigated, functionality and data integrity verified. **Rollback:** restore old image only for compatible data; for irreversible migrations restore the tested data copy. One application per rollout; no shared mass rollback.

## Batch 3 — extend CrowdSec beyond the canary

**Files:** `security/crowdsec/20-envoy-canary.yaml`, relevant route SecurityPolicies, `argo-cd/applications/crowdsec-app.yaml`.

Do not repeat the completed v1.8.1 upgrade. Patch remaining dependencies through a tested newer build when available; current bouncer also has high findings.

1. Make bouncer availability suitable for production: two replicas across nodes, disruption budget, readiness and stale-cache monitoring. Verify cold-start LAPI failure behavior; previous tests covered only a warm cache outage.
2. Choose one low-impact production route. Preserve/compose any existing route authorization policy; do not accidentally replace PHP source restrictions, OIDC or other auth.
3. Test legitimate request, banned client, spoofed forwarding headers, independent control client, bouncer failure and recovery. Confirm ordinary client identity from a separate WAN connection and any trusted proxies. LAN-only results are insufficient.
4. Roll out by route group; retain independent management access. Validate login/callbacks, WebSockets, APIs and federation as appropriate.

**Pass:** explicit allow/deny evidence on a production route, preserved source IP, alert delivery and fail-closed behavior accepted for that route. **Rollback:** revert only the new extAuth attachment, preserving existing authentication/source restrictions; remove temporary decisions and restore fault-injected settings.

## Batch 4 — close lateral movement and admission gaps

Start with sensitive data services and agent namespaces, not core networking/storage. Prioritize Hermes, Fleet, Odysseus, Backoffice and Portainer among the 31 namespaces without NetworkPolicy. Preserve explicitly requested Hermes management capabilities while reducing who can reach it and what non-management components can access.

- Map actual ingress, DNS, databases, queues, monitoring, backup and outbound API dependencies. Add explicit allows then default-deny, namespace by namespace. Remove overlapping allow-all rules after testing, since policies are additive.
- Restrict direct LoadBalancer/NodePort services (Ollama, Loki, InfluxDB, Hermes webhook) at network/service boundaries. Verify external consumers before changing service types; an internal hostname is not an access control.
- Extend no-privileged/no-host-access enforcement to compatible application namespaces after admission dry runs. Inventory hardware/storage exceptions by workload; do not enforce the same rules on CSI, CNI or radio device workloads blindly.
- Disable service-account automount for workloads that do not use Kubernetes. Replace broad application roles with explicit verbs/resources. Record necessary privileged access with review dates.
- Progress mutable-image/digest policies from audit to enforce for tested namespaces; pin versions and digests in the actual Kustomize/Helm source of truth.

**Pass:** legitimate dependency tests succeed; unrelated pod ingress/private egress and forbidden admission tests fail. **Rollback:** revert the individual restrictive policy while preserving earlier working controls. Monitor errors for 30 minutes and reassess after 24 hours.

## Batch 5 — trust stores, remaining scans, stateful services

- Complete eMudhra effective-trust inventory across node OS and application OS/Python certifi/Java/Node/custom bundles. Verify fingerprints from authoritative advisory data. Rebuild affected images; never bypass certificate validation. Prior absence in FreshRSS/MISP system bundles does not cover other stores.
- Triage 42 unmatched main and eight init digests: distinguish private-registry auth, platform/index mismatch, newly deployed images, oversized reports and actual scan failure. Prioritize Hermes agent, biometric processing, auth databases and MISP core. Preserve raw reports privately.
- Pilot scans with unfixed findings included, then change the operator's `ignoreUnfixed: true` after verifying archive capacity, report volume and alert behavior. Keep aggregate metrics and archive retention healthy. Audit CISA KEV/vendor exploitability for exact current findings before setting incident urgency; this pass has not established KEV matches.
- Patch stateful infrastructure only after a separate restore rehearsal. Immich: preserve PostgreSQL major/extension compatibility. OpenCTI: migrate from legacy RabbitMQ image using supported queue/data conversion, verify OpenSearch clients/indexes. Longhorn: verify replica/backup health, identify volumes still using old engines, follow supported manager/engine sequence, do not delete engine images still in use.
- Investigate or formally retire offline node-12 without assuming its workloads/data are expendable. Validate node/kernel/RKE2 advisories separately before a rolling node upgrade.

**Pass:** every high-risk current digest has fresh evidence or a tracked blocker; fixed candidates tested; data restored in an isolated rehearsal; required trust roots absent and expected TLS connections still validate. **Rollback:** use application-specific backup/restore, not blind database or storage binary downgrade.

## Execution contract

Use one reviewed commit/change set per batch or application. Keep unrelated local work intact. Resolve immutable digests at execution time; never invent fixed-image tags. Follow main-based GitOps ownership and check reconciliation after pushing. Avoid printing raw scan outputs, Secret objects, private configuration exports or credential-bearing logs.

A batch closes only when its change is deployed, functionality passes, targeted vulnerabilities are rechecked, rollback is documented, and evidence is saved. Stop that rollout on data-integrity errors, loss of admin access, unexplained migration failures, new sustained 5xx/auth failures or missing telemetry. A successful rollout alone is not a security verification.

Recommended first executable change set: Argo CD compatibility/rendering tests and v3.5.6 pin, with a separate Wallabag mitigation check and candidate-build job. Preserve the existing CrowdSec pilot until production rollout prerequisites pass.

## Sources checked

- [Argo CD v3.5.6 release](https://github.com/argoproj/argo-cd/releases/tag/v3.5.6)
- [Kustomize remote-ref advisory, CVE-2026-113708](https://github.com/argoproj/argo-cd/security/advisories/GHSA-9v9p-x54c-58gc)
- [Delete-hook project bypass, CVE-2026-77459](https://github.com/argoproj/argo-cd/security/advisories/GHSA-fmxq-cgp8-87wp)
- [Helm config-home advisory; conditional applicability](https://github.com/argoproj/argo-cd/security/advisories/GHSA-fw5c-w8rc-j7fx)
- [Wallabag upstream PR, still open when checked](https://github.com/wallabag/docker/pull/484)
- [Wallabag PHP vulnerability discussion](https://github.com/wallabag/wallabag/issues/9094)
- Local evidence: `posture-2026-10-25.json`, live archive metrics, selected Argo config/network policies, runtime images, and `IMPLEMENTATION-2026-10-24.md` for earlier pilot tests.
