# Executable patch queue — October 26, 2026

This updates the October 25 plan using the deployed changes and tests in PATCHING-PROGRESS-2026-10-26.md. Earlier entries in that ledger are superseded by later evidence. Fleet counts remain dated observations, not continuous security assurance. No evidence establishes absence of compromise.

## Deployed and verified, with remaining limits

- Argo CD v3.5.6 is pinned and deployed. Main binary uses Go1.26.10; downloaded Git LFS, Kustomize, Helm and KSOPS contain older toolchains/dependencies. Candidate helper rebuild is in progress. Existing admin/SSO observation and broad argocd-server ingress restriction remain open.
- Pages uses the reviewed immutable rebuilt image. Restored-copy content matched 1,102 files; production content matched after rollout; authenticated MCP, unauthorized rejection, static serving and strict-host-key Git access passed. Preserved an old stale Git lock and verified mirror remote equals local HEAD after restart. Original PVC and recovery snapshot retained. Candidate scan has no critical findings, but 49 high findings without listed fixes remain for disposition.
- Wallabag uses the pinned PHP8.4.27 runtime and updated compatible dependencies. FPM upload progress is Off, API token absent, HTTPS works, database integrity and pre/post table counts pass. Isolated restored-copy login, long-form article extraction and export passed on the final image. Scan remains 1 critical/16 high in legacy application libraries. The X509 feature implicated by the critical finding is not configured; that does not patch the dependency. Guzzle5/Symfony4.4 modernization remains open.
- CrowdSec canary has independently credentialed, node-separated bouncers using the initial-sync authorization fix and patched toolchain. Per-identity bans/spoofing/deletions, initial failure/recovery, replica eviction (10/10 HTTP200) and all-backends-down blocking/recovery have evidence. All-down returned HTTP500, not the test's expected403; requests were blocked. Production routes remain unattached pending stale-cache, WAN identity and alert-delivery gates.

## Next changes in execution order

| Order | Change | Required proof |
|---|---|---|
| 1 | Finish Argo runtime candidate | Build Git LFS3.8.1, Kustomize5.8.3, Helm4.3.1 and KSOPS4.5.1 with Go1.27.3. Scan exact image/platforms, test Helm/Kustomize/encrypted manifests and Git operations, remove old KSOPS init mount, deploy pinned digest and verify all components/auth/reconciliation. Keep live Argo unchanged until candidate gates pass. |
| 2 | Finish Wallabag legacy dependency remediation | Review reachable Guzzle5/Symfony/OTPHP findings; migrate to supported code/dependencies or verified backports with real login/import/extraction/export/migration tests. Do not equate the deployed runtime fix with complete patching. Preserve existing network/source restrictions and fresh private recovery copies. |
| 3 | Rebuild sensitive applications | Condo, MISP modules, OpenClaw/Congress, CompreFace and browser/parser/LAN integrations including FlareSolverr, Kavita and Homebridge; include Synapse. Match running digests, use current vendor advisories, build fixed candidates and test each app separately. Preserve Brain-owned Pages content. |
| 4 | Enforce lateral-movement and workload restrictions | Map Hermes/Fleet/Odysseus/Backoffice/Portainer dependencies; add tested default-deny with explicit allows; review direct services including Qwen/Ollama/Loki/InfluxDB/Hermes; remove unused API tokens and narrow RBAC. Move compatible admission policies to Enforce/Fail after rejection/allow tests. Preserve intended management/hardware permissions and independent access. |
| 5 | Expand CrowdSec to production | Add bounded stale-cache behavior; verify WAN/proxy client identity and actual alert delivery. Start with one low-impact route, preserve existing auth/source restrictions, test legitimate/denied traffic, callbacks, failures and recovery. Roll out by route group only after evidence passes. |
| 6 | Complete trust and scan coverage | Verify effective OS/Go/Python/Java/Node/custom trust stores for eMudhra; never bypass TLS validation. Reconcile missing main/init/ephemeral digests against CRDs and archives; preserve oversized outputs; upgrade scanner/operator with compatibility/capacity checks; include unfixed findings and test freshness/coverage alert delivery. |
| 7 | Stateful and node maintenance | Rehearse restores before Immich PostgreSQL/extensions, RabbitMQ/OpenSearch and Longhorn changes. Follow supported migrations, retain old volumes/replicas until recovery is proven, investigate node-12 ownership before retirement, and review RKE2/kernel advisories with attended rolling-upgrade/quorum tests. |

## Execution and rollback

One application/change set per commit. Fetch main before pushing; keep unrelated original-checkout changes intact. Recovery data, credentials and raw scan archives stay private. Each batch needs baseline, recovery artifacts, candidate scan, functional and denial tests, deployed digest/revision checks and rollback instructions. Database migrations require a tested restore, not a blind image downgrade. Observe each rollout for sustained failures and reassess error trends after 24 hours when operationally arranged; this document does not schedule monitoring.

Full patching remains incomplete. Existing authorizations cover upgrades, pins, commits/pushes and necessary PRs; do not wait on the previously invisible PR form. Escalate only genuinely destructive choices or unresolved maintenance impacts.
