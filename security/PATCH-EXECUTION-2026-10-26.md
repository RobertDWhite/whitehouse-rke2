# Cluster patch execution plan

Assessment refreshed October 26, 2026, 15:17 UTC. This is the current execution order; older plan entries and the progress ledger retain historical evidence. Full patching remains incomplete. This refresh made no production changes.

## Verified priorities

| Finding | Evidence and scope | Priority |
|---|---|---|
| Application identities can administer the cluster | Live authorization checks for `hermes/hermes-mcp` and `portainer/portainer-sa-clusteradmin` allow Secret reads across namespaces, creation of ClusterRoleBindings and pod exec. No Secret contents were read. | P0: reduce compromise impact |
| IntelOwl ships vulnerable runtime packages and an unsupported framework | Running main digest still matches today's full scan: 257 critical / 3,271 high occurrences; Nginx has 43 high. Source pins Django 4.2.27, Python 3.11.7 and Celery 5.4.0. Django 4.2 security support ended April 7, 2026. | P0: rebuild and migrate |
| Several applications lack network isolation | 21 running namespaces have no NetworkPolicy. Application targets include watch-badges, Yeti, HA Screen, ground-station and Scrypted; infrastructure namespaces need separate designs. Policy absence does not prove Internet exposure. | P0: contain lateral movement |
| MISP candidate is not ready for deployment | Production modules remain v3.0.6. Retained scan: 23 critical / 273 high; v3.1.1 candidate: 7 critical / 126 high. PR482 built successfully but did not publish images. | P1: finish image and recovery gates |
| Maintenance dependencies remain impaired | Six nodes Ready; node12 Ready=Unknown. `onepassword-shared` Ready=False / InvalidProviderConfig. | Gate dependent restarts and host maintenance |

Raw counts include duplicated package occurrences. For example, IntelOwl's build headers account for 1,312 fixable high/critical occurrences, but ImageMagick runtime packages also have fixable critical findings. Removing headers alone does not resolve the runtime risk. The scan identifies installed ImageMagick `8:6.9.11.60+dfsg-1.6`; Debian confirms a fixed Bookworm package for CVE-2025-53014. Exploit reachability through enabled analyzers remains to be tested.

Private evidence: `~/Library/Application Support/whitehouse-security/assessment-refresh-20261026/execution-plan-live-refresh.json`; full image JSON and checksum receipts in `candidate-scans-20261026`. The live inventory includes validation pods, so 471 running pods is not a production-only denominator. No new full-cluster Trivy scan was performed in this refresh.

## Batch 1: reduce access while images build

1. **Watch-badges, then Yeti, then HA Screen:** map live gateway, monitor and application/backend flows. Add ingress default-deny and exact source-pod/port allowances, one namespace per commit. Sources: `apps/misc/watch-badges`, `apps/misc/yeti`, `apps/home/ha-screen`; inspect any overlapping policy owner before editing. Yeti's API/workers, Redis and ArangoDB require separate allow rules. Do not permit whole gateway or monitoring namespaces.
2. **Hermes:** inspect `apps/misc/hermes/12-mcp-rbac.yaml` and actual tool operations. Separate the callback-facing identity from administrative execution. Replace cluster-admin with the smallest tested roles; workload creation, exec and RBAC mutation must be considered escalation paths, not merely API permissions.
3. **Portainer:** establish its owning manifest/Helm source and required operational scope, then replace its broad binding with scoped access. Preserve an independent administrator recovery path.
4. Disable automatic service-account token mounting for apps that do not call Kubernetes, after checking both Pod and ServiceAccount settings and startup behavior. An omitted Pod field alone does not establish token exposure.

Acceptance: unrelated same-namespace and cross-namespace pods cannot reach protected app/database ports; real ingress, monitoring and app workflows still work. For RBAC, repeat the current authorization checks and prove unrelated Secret reads, RBAC changes and exec are denied; test required real workflows too. Compare live policy and role specs with GitOps source. Roll back only the affected commit if a legitimate flow fails.

## Batch 2: IntelOwl runtime and dependency remediation

Keep the deployed ingress containment and native uWSGI proxy repair. Production main image is `intelowlproject/intelowl:v6.8.0@sha256:b4807c96be23c5a52aa126cce5108a713a0954d64ea0f4bdda169322fce3981f`; Nginx is `intelowlproject/intelowl_nginx:v6.8.0`, scanned digest `sha256:d79e3bef1116f11f5adda18392cd6b864c636d14f5c597bbffee65c61a3f3a4d`.

- **2A — proxy image:** rebuild `docker/Dockerfile_nginx` from a current patched, digest-pinned supported Nginx/Alpine base. Preserve log-file semantics and the healthcheck. This can be tested independently of the Django migration. Scan the final image, test config syntax, gateway UI plus all eight referenced assets, anonymous `/api/jobs` denial, native uWSGI and WebSocket routing. A web Pod rollout also restarts Django; check migrations and recovery before rollout.
- **2B — app runtime:** refresh the supported Python patch/base, rebuild OS packages including ImageMagick, and separate build-only headers/tools from runtime only after identifying analyzer dependencies. Lock build inputs; do not dynamically install changing packages at startup.
- **2C — supported framework:** evaluate Django **5.2.18 LTS** as the current supported migration target, not an already accepted version. Resolve `certego-saas`, auth, REST framework, Celery integration and analyzer compatibility. A last 4.2 patch would still leave an unsupported framework. Submit a focused upstream compatibility PR if upstream has no suitable release.
- **2D — worker compatibility:** test a supported stable Celery/Kombu combination against RabbitMQ4.4. Current 5.4.0 control inspection fails on transient non-exclusive queues. Stable Celery5.6 documents `control_queue_exclusive` and `control_queue_durable`; newer development defaults differ. Validate explicit supported queue settings in a disposable broker/worker topology, including reconnect and reply queues. Do not copy development defaults blindly or re-enable RabbitMQ's deprecated feature as the fix.

Acceptance: full exact-image scan, package/version inventory, database/files restore rehearsal, migration forward/recovery check, login/API authorization, safe file/URL analyzer fixtures, queued job completion and result retrieval, worker restart/reconnect, WebSocket behavior, TLS positive/negative tests and repeat network denials. Do not use broker connection success or readiness as proof that tasks run. Treat entrypoint migrations and notifications as side effects when designing the isolated rehearsal.

Rollout source: `apps/misc/intelowl/kustomization.yaml` and application manifests. Pin accepted version plus digest for every changed image, including init images. Update one component per reviewable commit where the shared Pod allows it. If a schema changes, binary rollback alone is insufficient: use the rehearsed recovery path.

## Batch 3: finish prepared builds, then patch the remaining high-risk apps

| Workload | Next executable step | Required acceptance |
|---|---|---|
| MISP | Publish a final modules image using already-fixed upstream dependencies and the runtime installer cleanup from [PR482](https://github.com/MISP/misp-docker/pull/482). Do not duplicate the upstream lock fix or wait on completed CI40337521702. Correct the ZMQ Kustomize image-name mismatch and choose a compatible Python version. | Scan all platform/runtime/vendor packages; rehearse database/files recovery; core login/API, permitted enrichment, private-destination rejection and ZMQ event delivery. Modules/core/ZMQ share a Pod. |
| Homarr | Build pushed dependency patch12ab71a after resolving fork workflow registration; existing sign-in handoff is pending. | Exact-image scan, restored startup, sanitization, authenticated integrations and explicit pins. Baseline restore alone is not candidate acceptance. |
| Homebridge | Build installer-removal patchd0c68ea through an available build path; upstream PR is not yet confirmed. | Scan, restored pairing/config and real accessory operations before promotion. |
| OpenClaw/Hermes, FlareSolverr | Fresh-scan deployed and supported candidate digests, prioritize exposed tool/browser/parser paths and credential access. | Real tool/challenge workflows, rejected unauthorized inputs, functional containment. Restore credential delivery first for affected restarts. |
| FreshRSS, Wallabag, Synapse/Hookshot, Calibre/Kavita, SDR, Congress | Reassess retained findings against exact running images and current supported releases; patch one app at a time. | App-specific authenticated flows; real encrypted Matrix events where relevant; import/processing tests for parsers. |

FreshRSS currently runs `1.30.4-ls327`; Wallabag runs the custom security image recorded in the progress ledger. Those upgrades must not be mistaken for full residual closure. Ombi is already upgraded and ingress-isolated; its remaining zlib finding needs a further image fix, not repetition of the completed rollout.

## Batch 4: prevent data exit and recurrence

- Map required outbound destinations and introduce dependency-only egress, starting with sensitive apps. Prove unrelated external and internal destinations are blocked. IntelOwl's analyzer egress remains unrestricted; its use case needs controlled destination handling rather than a blanket rule that breaks enrichment.
- Verify eMudhra/emSign remediation in each actual runtime trust store, including Node, Python/certifi, Go, Java and custom bundles. Match the affected root by fingerprint using retained primary evidence; prove valid chains succeed and invalid chains/wrong hostnames fail. Preserve Glacier's deployed backend TLS verification.
- Expand tested admission enforcement beyond Backoffice to immutable images, allowed registries, no privilege escalation, non-root where supported and restricted host access. Include init/ephemeral containers. Stage workload and webhook-failure tests before broader fail-closed enforcement.
- Reconcile scanner coverage across main/init/ephemeral images, index/platform digests, freshness, archived-only reports and partition completeness. Preserve full findings and the two-minute completed scan-job TTL. An unmatched digest is a reconciliation task, not automatically an unscanned image.
- Prove Falco/Trivy alerts reach the final notification destination and CrowdSec blocks a controlled request on an attached route while normal traffic works. Alertmanager receipt and an isolated canary alone are incomplete delivery/production protection evidence.
- Repair node12 and secret-provider delivery; verify storage redundancy, backup restoration and quorum before PostgreSQL/extensions, RabbitMQ/OpenSearch, Longhorn, RKE2 or kernel maintenance. Separate these from app dependency migrations.

## Completion record for every patch

Record baseline/candidate image digests, scan timestamp/database metadata, fixed and residual findings, backup/restore evidence, actual functional and denial tests, commit/push result, reconciled Argo revision and running digest. Every retained critical/high finding needs a package path, applicability assessment, compensating control and review date. Never filter findings merely to make totals green. Keep credentials, backup data and full sensitive reports outside Git and upstream PRs.

## Primary references checked in this refresh

- [Django supported versions](https://www.djangoproject.com/download/): 4.2 unsupported; 5.2 LTS supported through April2028, current patch5.2.18.
- [Celery stable queue configuration](https://docs.celeryq.dev/en/stable/userguide/configuration.html#control-queue-exclusive): durable/exclusive controls and incompatibility of enabling both.
- [Debian CVE-2025-53014 tracker](https://security-tracker.debian.org/tracker/CVE-2025-53014): installed ImageMagick version is below the Bookworm fixed package; reachability is a separate test.

Detailed completed changes and limitations remain in `PATCHING-PROGRESS-2026-10-26.md`. Start execution with Batch1 containment and IntelOwl2A candidate preparation; continue independent batches while build or recovery gates are pending.
