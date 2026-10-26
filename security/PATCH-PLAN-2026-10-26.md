# Executable patch queue — October 26, 2026

This updates priorities in PATCH-PLAN-2026-10-25.md using the attended rollout and failure tests recorded in PATCHING-PROGRESS-2026-10-26.md. The October 25 fleet counts are a baseline, not a new full-cluster scan. No evidence here establishes absence of compromise.

## Completed changes, with remaining limits

- Argo CD: pinned v3.5.6 digest deployed across five workloads; desired replicas ready; application Synced/Healthy; gateway TLS and version endpoint pass; representative KSOPS rendering and subsequent reconciliation pass. Fresh image report still has 2 critical and 120 high findings in bundled dependencies. Interactive SSO and the full observation window remain open verification gates.
- Wallabag: global PHP upload-progress disabled, unused service-account token removed, existing image pinned by multi-platform digest. Live FPM confirms Off/Off; database quick_check passes; gateway returns 302; application Synced/Healthy. Consistent private SQLite backup integrity passes. PHP 8.1.32 remains unsupported and vulnerable; this is mitigation only. Initial generated-configuration reference error was corrected and final runtime verified.
- CrowdSec bouncer: two available replicas on distinct nodes, zero-unavailable rolling updates and minAvailable=1 disruption budget. Existing canary responds normally. Production routes remain unattached.

## Next changes in execution order

| Order | Change | Required proof before rollout/closure |
|---|---|---|
| 1 | Fix CrowdSec cold-start authorization bypass | Same-version isolated test repeatedly allowed requests while initial LAPI was unavailable and readiness was false. Add a check in the authorization path so an uninitialized cache returns a denial/error. Test initially unavailable LAPI, successful empty initial sync, real bans, spoofed headers, recovery, and stale-cache timeout. Produce reviewed upstream/fork changes and a scanned immutable image. Do not attach production routes until these pass. |
| 2 | Build and deploy supported Wallabag runtime | Existing upstream PR #484 passes four database CI jobs but is still open. Build reviewed source with supported PHP and startup upload-progress mitigation; scan image. Restore the private SQLite backup into an isolated instance, test login/import/extraction/export and migrations, then pin digest in apps/misc/wallabag/kustomization.yaml. Update PHP configuration mount path for the new runtime. Keep existing network isolation. |
| 3 | Remove Argo helper/KSOPS vulnerabilities | Identify binary paths responsible for CVE-2025-68121 in fresh archive reports. Rebuild with fixed supported Go toolchain; v4.5.1 KSOPS is not clear. Re-run KSOPS, Helm and Kustomize rendering against the candidate before updating pins. Restrict broad direct server ingress in a separate dependency-tested change. |
| 4 | Rebuild sensitive application images | Condo, MISP modules, Pages, OpenClaw/Congress, then CompreFace/browser/parser workloads. Resolve actual source repositories and running digests; scan candidates and test auth, uploads, workers, enrichment and MCP functions separately. Preserve Brain-owned Pages source ownership. |
| 5 | Enforce lateral-movement and workload restrictions | Map dependencies for Hermes, Fleet, Odysseus, Backoffice and Portainer. Add explicit ingress/egress allows then default deny, one namespace at a time. Verify allowed dependencies and denied unrelated/metadata/API access. Review direct LoadBalancer exposure. Extend admission enforcement only after dry runs; preserve intentional management and hardware permissions. |
| 6 | Expand CrowdSec to production | Depends on step 1. Start with one low-impact route, preserve its existing authentication/source policy, verify source identity from WAN, bans, legitimate requests, outages/recovery, callbacks and alert delivery. Advance by route group only after evidence passes. |
| 7 | Complete trust, scan and stateful work | Inventory effective OS/language/node trust stores for eMudhra; repair unmatched current-image scans and include unfixed findings with capacity checks. Rehearse restores before Immich Postgres, OpenCTI RabbitMQ/OpenSearch and Longhorn upgrades. Review offline node-12 and RKE2/kernel advisories independently. |

## Execution and rollback

Use one application/change set per commit, fetch main before pushing, and reconcile the intended revision. Keep the original checkout's unrelated home-screen change intact. Recovery exports and databases stay in owner-only private storage; never add raw secrets or private scan archives to Git. Do not bypass TLS verification.

Each batch needs a known-good baseline, preserved data, candidate scan, runtime test, deployed digest/revision check and documented rollback. Database-changing updates require a tested restore rather than an image downgrade. Network-policy rollback removes only the new restriction. A running pod or successful GitOps sync does not prove that an authorization control blocks requests.

Full patching is not complete. The newly observed CrowdSec cold-start allow behavior is a release blocker for wider blocking coverage, not a reason to disable the existing canary or postpone application patching.

Sources: local live-test ledger; https://github.com/kdwils/envoy-proxy-bouncer/blob/v0.8.2/decisions/cache.go; https://github.com/wallabag/docker/pull/484; https://www.php.net/archive/2026.php#2026-10-23-1. Other baseline evidence and vendor references remain in the October 25 plan.
