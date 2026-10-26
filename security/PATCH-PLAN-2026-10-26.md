# Executable patch queue — October 26, 2026

This updates the October 25 plan using the deployed changes and tests in PATCHING-PROGRESS-2026-10-26.md. Earlier entries in that ledger are superseded by later evidence. Fleet counts remain dated observations, not continuous security assurance. No evidence establishes absence of compromise.

## Deployed and verified, with remaining limits

- Argo CD v3.5.6 is pinned and deployed. Main binary uses Go1.26.10; downloaded Git LFS, Kustomize, Helm and KSOPS contain older toolchains/dependencies. Candidate helper rebuild passed rendering comparisons, but its full scan still identifies 52 high findings per architecture; see the fresh evidence below. Existing admin/SSO observation and broad argocd-server ingress restriction remain open.
- Pages uses the reviewed immutable rebuilt image. Restored-copy content matched 1,102 files; production content matched after rollout; authenticated MCP, unauthorized rejection, static serving and strict-host-key Git access passed. Preserved an old stale Git lock and verified mirror remote equals local HEAD after restart. Original PVC and recovery snapshot retained. Candidate scan has no critical findings, but 49 high findings without listed fixes remain for disposition.
- Wallabag uses the pinned PHP8.4.27 runtime and updated compatible dependencies. FPM upload progress is Off, API token absent, HTTPS works, database integrity and pre/post table counts pass. Isolated restored-copy login, long-form article extraction and export passed on the final image. Scan remains 1 critical/16 high in legacy application libraries. The X509 feature implicated by the critical finding is not configured; that does not patch the dependency. Guzzle5/Symfony4.4 modernization remains open.
- CrowdSec canary has independently credentialed, node-separated bouncers using the initial-sync authorization fix and patched toolchain. Per-identity bans/spoofing/deletions, initial failure/recovery, replica eviction (10/10 HTTP200) and all-backends-down blocking/recovery have evidence. All-down returned HTTP500, not the test's expected403; requests were blocked. Production routes remain unattached pending stale-cache, WAN identity and alert-delivery gates.

## Next changes in execution order

| Order | Change | Required proof |
|---|---|---|
| 1 | Finish Argo runtime candidate | The first candidate builds Git LFS3.8.1, Kustomize5.8.3, Helm4.3.1 and KSOPS4.5.1 with Go1.27.3, but is not the final patch. Rebuild or safely remove the inherited pebble binary after inspecting startup use; update vulnerable Go modules in Argo, Git LFS and KSOPS with compatibility tests. Scan exact image/platforms, test Helm/Kustomize/encrypted manifests and Git operations, remove old KSOPS init mount, deploy pinned digest and verify all components/auth/reconciliation. Keep live Argo unchanged until candidate gates pass. |
| 2 | Finish Wallabag legacy dependency remediation | Review reachable Guzzle5/Symfony/OTPHP findings; migrate to supported code/dependencies or verified backports with real login/import/extraction/export/migration tests. Do not equate the deployed runtime fix with complete patching. Preserve existing network/source restrictions and fresh private recovery copies. |
| 3 | Rebuild sensitive applications | Condo, MISP modules, OpenClaw/Congress, CompreFace and browser/parser/LAN integrations including FlareSolverr, Kavita and Homebridge; include Synapse. Match running digests, use current vendor advisories, build fixed candidates and test each app separately. Preserve Brain-owned Pages content. |
| 4 | Enforce lateral-movement and workload restrictions | Map Hermes/Fleet/Odysseus/Backoffice/Portainer dependencies; add tested default-deny with explicit allows; review direct services including Qwen/Ollama/Loki/InfluxDB/Hermes; remove unused API tokens and narrow RBAC. Move compatible admission policies to Enforce/Fail after rejection/allow tests. Preserve intended management/hardware permissions and independent access. |
| 5 | Expand CrowdSec to production | Add bounded stale-cache behavior; verify WAN/proxy client identity and actual alert delivery. Start with one low-impact route, preserve existing auth/source restrictions, test legitimate/denied traffic, callbacks, failures and recovery. Roll out by route group only after evidence passes. |
| 6 | Complete trust and scan coverage | Verify effective OS/Go/Python/Java/Node/custom trust stores for eMudhra; never bypass TLS validation. Reconcile missing main/init/ephemeral digests against CRDs and archives; preserve oversized outputs; upgrade scanner/operator with compatibility/capacity checks; include unfixed findings and test freshness/coverage alert delivery. |
| 7 | Stateful and node maintenance | Rehearse restores before Immich PostgreSQL/extensions, RabbitMQ/OpenSearch and Longhorn changes. Follow supported migrations, retain old volumes/replicas until recovery is proven, investigate node-12 ownership before retirement, and review RKE2/kernel advisories with attended rolling-upgrade/quorum tests. |

## Execution and rollback

One application/change set per commit. Fetch main before pushing; keep unrelated original-checkout changes intact. Recovery data, credentials and raw scan archives stay private. Each batch needs baseline, recovery artifacts, candidate scan, functional and denial tests, deployed digest/revision checks and rollback instructions. Database migrations require a tested restore, not a blind image downgrade. Observe each rollout for sustained failures and reassess error trends after 24 hours when operationally arranged; this document does not schedule monitoring.

Full patching remains incomplete. Existing authorizations cover upgrades, pins, commits/pushes and necessary PRs; do not wait on the previously invisible PR form. Escalate only genuinely destructive choices or unresolved maintenance impacts.

## Fresh review — 2026-10-26 05:48 UTC

Read-only inventory: 467 Running pods, 262 distinct main-container runtime digests, 211 matched to available vulnerability-report digests (80.5%). The remaining 51 are unresolved coverage matches, not necessarily unscanned images. Init/ephemeral images, index/platform aliases, report age and private archives still need reconciliation. Temporary validation workloads are included in this snapshot. Raw evidence is owner-only in `~/Library/Application Support/whitehouse-security/posture-refresh-20261026.json`.

### Candidate scan changes the immediate Argo plan

Candidate `ghcr.io/robertdwhite/argocd-security-runtime:sha-d161c89d080443cb8aa0f56e1ddb1c326ff59d6a@sha256:9f7a0314affc3d364a59c7a6bffa834bbd9f3a779a3a5ea229d82631974f08b7` built successfully. Both amd64 and arm64 full scans report 0 critical, 52 high, 101 medium, 9 low and 6 unknown occurrences. All 52 high occurrences have listed fixes. Counts are scanner occurrences, not distinct exploitable paths.

| Binary | High occurrences | Required follow-up |
|---|---:|---|
| `/usr/bin/pebble` | 26 | Go1.26.2 and x/net0.40.0 remain, including CVE-2026-111943. Identify startup/runtime use, then rebuild with patched toolchain/modules or remove only after proving it unused. |
| `/usr/local/bin/argocd` | 2 | Update klauspost/compress and x/net with Argo regression tests or use a verified upstream fixed build. |
| `/usr/local/bin/git-lfs` | 3 | Update x/crypto, x/net and x/text; exercise authenticated Git/LFS operations. |
| `/usr/local/bin/ksops` | 21 | Update x/crypto, x/net, x/text and gRPC; exercise SOPS decryption and supported key backends. |

The exact candidate matches baseline FreshRSS, MISP and Wallabag Kustomize output and the Argo Helm chart/custom-values output. Decrypted manifests were neither printed nor retained. Live self-application source type is Kustomize. This establishes rendering compatibility only; it does not satisfy remaining scan, Git, authentication or rollout gates. Production Argo was not changed during this review.

### Priority application queue from currently matched reports

Reports were refreshed between October 25 and 26; these are current-running-image matches to existing reports, not fresh rescans of every application. Confirm vendor fixes and reachability before assigning exploitability. Prioritize credentials and data access alongside counts.

| Target | Critical / high occurrences | Concrete patch batch and acceptance test |
|---|---:|---|
| Condo v5.23.0 | 37 / 614 | Rebuild pinned runtime/OS and application dependencies; restored PostgreSQL clone; login, resident/property operations, worker and address-service tests; migration rollback by restore. |
| FlareSolverr v3.5.2 | 28 / 899 | Fixed browser/runtime image; isolate parser egress from LAN/control plane; real media integration challenge-resolution test. |
| Kavita nightly-0.8.9.45 | 24 / 372 | Supported fixed build; backed-up library database; login, library scan and reader tests. |
| Homebridge 2026-05-13 | 24 / 363 | Updated runtime/plugins with config backup; pairing and physical accessory control; preserve required LAN discovery. |
| MISP modules v3.0.6 | 21 / 193 | Rebuild module dependencies separately from MISP core; module discovery and representative enrichment tests with private data kept local. |
| OpenClaw custom image | 21 / 145 | Rebuild runtime/dependencies; inspect mounted credentials and tool permissions; test authorized tools and denied actions. |
| Immich PostgreSQL14/extensions | 20 / 123 | Supported database/extension migration on restored copy; asset metadata/search tests and restore timing before production maintenance. |
| OpenCTI RabbitMQ4.1.2 | 18 / 78 | Supported broker upgrade with durable queue recovery and producer/consumer tests. |
| OpenCTI OpenSearch3.5.0 | 16 / 432 | Supported upgrade with tested snapshot restore and indexing/query tests. |
| Synapse1.160.0 | 16 / 111 | Fixed candidate with database recovery rehearsal; login, federation as applicable, encrypted messaging and appservice tests. |

### Containment batches to execute alongside image work

1. **Management access first:** `argocd-server` currently has `ingress: [{}]`. Replace this broad allow with explicit gateway/authorized management/metrics paths. Verify gateway/API/SSO and reconciliation work, and an unrelated namespace cannot connect. Preserve an independent recovery path. Existing repo-server and Redis selectors provide narrower ingress; test their effective denial rather than claiming they are openly reachable.
2. **High-impact namespaces:** 31 running namespaces have no NetworkPolicy. Begin with Hermes, Fleet, Odysseus, Backoffice and Portainer. Record required source/destination/port flows, add explicit allows plus ingress/egress default-deny, then test authorized operations and denied cross-namespace/API/LAN traffic. Infrastructure and hardware services need separate exceptions; do not blanket-deny them.
3. **Direct entry points:** review Ollama11434, Qwen8000, Loki3100, InfluxDB8086 and Hermes webhook8645. Their direct LoadBalancer Services may bypass gateway controls. Establish actual LAN/WAN/Tailscale reachability and native authentication, then restrict sources or remove unnecessary direct listeners. A LoadBalancer alone is not proof of Internet exposure.
4. **Permissions:** `hermes-mcp-admin`, `portainer` and `velero-server` have exact cluster-admin bindings. Inventory the operations they need, replace with scoped roles where feasible, and test positive and negative authorization. Review equivalent wildcard permissions and escalation paths too. Leave system bootstrap bindings untouched until their controller lifecycle is understood.
5. **Admission:** disallow-host-path, disallow-mutable-image-tags, disallow-privileged-containers, require-image-digest and restrict-image-registries are Audit. Only php-containment-host-access is Enforce. Remediate violations and define narrow documented system exceptions; stage enforcement by namespace. Pass both allowed-workload admission and deliberate prohibited-workload rejection tests, plus controller recreation. Include API-token defaults, seccomp, capability drops and non-root/read-only-root-filesystem compatibility.
6. **Detect and block:** finish CrowdSec stale-cache expiry, trusted client-IP and alert-delivery tests before production attachment. Verify Falco signal delivery end-to-end with a harmless synthetic event. Do not describe an alert rule merely being loaded as successful delivery.
7. **Trust/coverage:** inventory effective trust stores and compiled runtimes, especially bundled binaries such as pebble. Check eMudhra mitigation without disabling certificate verification. Reconcile every deployed image class and architecture with scan evidence; alert on stale/missing scans and test alert delivery.

### Per-batch execution contract

- Record baseline image digest, Argo revision, application checks and data recovery location before mutation.
- Render version/digest pins in Kustomize; inspect all main and init container images. No `latest` or unpinned replacement.
- Archive the exact candidate's full scan privately, including unfixed findings. Investigate every remaining critical/high finding; a lower total alone is not acceptance.
- Run relevant restore, functional, authentication and deny tests. Stop rollout for failed tests, unexplained content/count changes, migration errors or lost recovery access.
- Commit one coherent batch, push, reconcile and confirm the running platform digest and source revision; check user-visible behavior, errors and security decisions.
- For image/config-only failures, revert that batch and reconcile. For database/schema changes, restore the tested backup into the retained recovery path; do not assume an old image can read migrated data.
- Retain evidence and recovery artifacts until post-change validation and observation pass. Keep residual risks explicit; do not mark the overall patch effort complete based on candidate success.

Policy approach follows the [Kubernetes security checklist](https://kubernetes.io/docs/concepts/security/security-checklist/) and [application checklist](https://kubernetes.io/docs/concepts/security/application-security-checklist/). The priority on GitOps credentials and component access follows [Argo CD security guidance](https://github.com/argoproj/argo-cd/blob/master/docs/operator-manual/security.md).
