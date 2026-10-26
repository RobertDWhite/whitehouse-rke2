# Cluster hardening and patch execution plan

Updated from live read-only review: **2026-10-26 06:55 UTC**. This replaces earlier candidate-status entries in this plan. Historical tests remain in PATCHING-PROGRESS-2026-10-26.md; older entries are not current deployment claims.

## Current evidence

- 469 Running pods; 264 distinct main-container runtime digests. 194 match currently available Trivy vulnerability-report digests (73.5%); **70 remain unmatched**. There are 737 report objects. This is a digest-match lower bound, not a fresh full-fleet scan or proof that unmatched images were never scanned. Index/platform aliases, report churn, init/ephemeral containers and private archives require reconciliation. Temporary validation pods are included.
- **30 running namespaces have no NetworkPolicy**, including Hermes, Fleet, Odysseus and Portainer. Policy presence elsewhere does not prove isolation: rules are additive and require traffic tests.
- Five general Kyverno policies are Audit: hostPath, privileged containers, mutable tags, required digest and registry restrictions. PHP host-access containment is Enforce. Audit does not block deployment.
- Read-only authorization checks confirm Hermes MCP, Portainer and Velero service accounts can get Secrets across namespaces and create ClusterRoleBindings. No Secrets were read. Treat compromised management workloads as potential cluster-wide credential/escalation paths. Backup permissions need a separate compatibility review.
- Direct listeners include Ollama11434, Qwen8000, Loki3100, InfluxDB8086 and Hermes8645. Establish actual LAN/Tailscale/WAN reachability and authentication; a LoadBalancer is not proof of Internet exposure.
- Argo, Backoffice, FreshRSS, MISP and Wallabag report Synced/Healthy at GitOps revision4bf381258c98a489d50a4cdd41bab45a88bca543. Ready Argo main workloads use the patched immutable runtime. Two old non-ready Argo pods are Failed/Evicted, not active old-runtime replicas.
- Subsequent execution: Backoffice API and web deployed and verified at3e8876d2. Both run source1aec0c9 immutable images; web token removed and both runtimes hardened. See the progress ledger for positive/negative checks and residual API findings.

Private snapshot: ~/Library/Application Support/whitehouse-security/posture-review-20261026-current.json (0600). Full scan and recovery files remain private. Findings do not establish exploitation or absence of compromise.

## Execute in this order

| Batch | Concrete work | Acceptance / stop gate |
|---|---|---|
| 1 — completed this execution | Deploy Backoffice API first, then web, using tested source1aec0c9 and the exact digests below. Apply non-root, seccomp, dropped capabilities, no privilege escalation, read-only root filesystem; web gets writable cache/tmp and no API token. Preserve API read-only Kubernetes token and existing tier isolation. | Reconfirm backup/restore evidence; render and server dry-run; API health, notes read, fresh cluster inventory and Services200; web primary pages contain expected data; public auth redirect/internal page work; unrelated ingress/egress remain denied. Database content/schema unchanged. Stop on any failed gate. |
| 2 — contain high-impact access | Inventory Hermes, Portainer, Fleet and Odysseus dependency flows. Add default-deny ingress/egress with explicit DNS/API/backend/gateway allows, one namespace at a time. Review direct listeners and remove or source-restrict unused exposure. Replace Hermes/Portainer cluster-admin with roles matching necessary operations; handle Velero separately. | Required workflows succeed; unrelated namespace/LAN/control-plane access fails; unnecessary Secrets and role-binding operations return Forbidden. Keep independent administrator access. Test backup and restore before narrowing Velero. |
| 3 — patch sensitive applications | Hermes and Condo first, then MISP modules and OpenClaw; finish Wallabag legacy libraries. Follow with parser/browser applications and Synapse, using matched findings below. Rebuild downstream only where upstream lacks a tested fixed image; submit generic upstream fixes when appropriate. | Verify upstream advisory applicability and supported target versions; build and scan immutable candidate on deployed architectures; test restored data and actual application workflows. Every remaining critical/high gets a documented disposition. |
| 4 — enforce deployment restrictions | Resolve violations in selected application namespaces, then stage the five Audit policies to Enforce with narrow system exceptions. Cover main/init/ephemeral image references. Disable unused API-token mounts and add compatible security contexts. | Valid workloads and controller recreation succeed; deliberately prohibited submissions are rejected by server admission. Confirm exceptions do not cover whole application namespaces. Roll back only the affected policy/namespace if legitimate recovery is blocked. |
| 5 — prove detection and perimeter blocking | Complete CrowdSec bounded stale-cache behavior, real WAN client-IP handling and end-to-end alert tests. Attach one low-impact production route, then expand by route group. Exercise Falco with a harmless synthetic event. | Legitimate/auth/callback requests succeed; bans and spoofed forwarding headers fail; cold-start and later backend failures fail closed with bounded recovery; alerts reach their destination. Keep existing authentication and source restrictions. |
| 6 — close trust/visibility gaps | Reconcile all70 unmatched main digests with platform aliases and private full reports; add init/ephemeral and architecture coverage. Test missing/stale-scan alerts and archiver throughput. Verify effective OS, Go, Python, Java, Node and custom trust stores for eMudhra mitigation. | Every in-scope digest has dated scan evidence or an explicit tracked exception; oversized reports retained privately; alert delivery tested. TLS verification remains enabled and intended valid chains work. Do not equate scanner absence with remediation. |
| 7 — stateful infrastructure | Rehearse supported Immich PostgreSQL/extensions, OpenCTI RabbitMQ/OpenSearch and Longhorn upgrades; review RKE2/kernel advisories and node ownership. | Fresh restore with application-level integrity tests and measured recovery time; supported migration path, quorum and rolling-maintenance checks. Keep original volumes/replicas until recovery is proven. |

Batches2 and6 should proceed alongside candidate build work: containment and visibility cannot wait for every upstream package release. Use separate coherent commits and avoid simultaneous changes to the same dependency path.

## Backoffice exact prepared candidates

Source PR: https://github.com/RobertDWhite/backoffice/pull/1 — merged2026-10-26T06:50:31Z.

- API: ghcr.io/robertdwhite/backoffice-api:sha-1aec0c9450436f230e782a0347ad24d407973d00@sha256:57cd134f4108e5cf6f5e8627d603954b46fa9cb4529d6ed09e6e71c03c12fd6a
- Web: ghcr.io/robertdwhite/backoffice-web:sha-1aec0c9450436f230e782a0347ad24d407973d00@sha256:881700ce5b38d34417064d32c7213ea7cb3ec3348f492466572c556a64916d39
- Prior candidate evidence: both architectures built; restored API health/synthetic note create-read-delete passed; web scans report no findings. API scans retain44high without listed fixes (0critical), chiefly OS packages. These require applicability disposition; hardening is mitigation, not a package fix.
- Source fixes the obsolete Kubernetes external-IP field used by Services. Events/Tunnel views have pre-existing missing backend endpoints and must not be claimed working.
- Deploy through apps/home/backoffice/kustomization.yaml; API/web manifests21-api-deployment.yaml and22-web-deployment.yaml. Do not restart or upgrade PostgreSQL in this batch.
- Preserve private backoffice-pre-runtime-20261026T061559Z backup. Isolated restored-copy resources, scan jobs, temporary allows and synthetic credentials were removed after verification; original PVC and private backups retained.

## Application patch queue from matched reports

Counts below are scanner occurrences, not distinct remotely exploitable vulnerabilities. Reports dated October25–26 match currently running digests; they are not newly executed scans in this review. Target versions must be selected from current vendor evidence and candidate tests, not guessed from tags.

| Application | Critical / high | Required functional gate |
|---|---:|---|
| Hermes v2026.7.7.2 (fresh Trivy0.76.1 scan) | 37 / 1054 | Private data restore; gateway tools/auth/dashboard, messaging platform state; resolve ExternalSecret error before restarting. |
| Condo v5.23.0 | 37 / 614 | Restored PostgreSQL; login, resident/property operations, workers/address service; migration recovery. |
| MISP modules v3.0.6 | 21 / 193 | Module discovery and representative enrichment; keep private indicators local. |
| OpenClaw custom runtime | 21 / 145 | Authorized tools work, denied tool/API operations fail; review mounted credentials. |
| FlareSolverr v3.5.2 | 28 / 899 | Actual media challenge resolution; restrict parser access to LAN/control plane. |
| Kavita nightly-0.8.9.45 | 24 / 372 | Backed-up library database; login, scan and reader. |
| Homebridge2026-05-13 | 24 / 363 | Config recovery, pairing and physical accessory control; retain needed discovery. |
| SDR SSTV0.3.0 | 17 / 308 | Recorded sample decode; review host/device permissions without breaking capture. |
| Calibre Web Automated4.0.8 | 17 / 126 | Restored library metadata; import, conversion, download and reader. |
| Synapse1.160.0 | 16 / 111 | Database restore; login, encrypted messaging, appservices and relevant federation. |
| Immich PostgreSQL14/extensions | 20 / 123 | Supported restored-copy migration; asset metadata and search integrity. |
| OpenCTI legacy RabbitMQ4.1.2 | 18 / 78 | Durable queue recovery and producer/consumer tests. |
| Longhorn engine1.10.0 | 14 / 214 | Supported manager/engine compatibility and restore; no ad hoc engine replacement. |

Concrete vendor-confirmed example: Condo's matched report contains Django5.2 and CVE-2025-64459, a potential SQL injection via QuerySet/Q connector handling. Django identifies5.2.8 as a fix for that advisory. Select a currently supported patched release covering subsequent advisories, not merely5.2.8; inspect whether untrusted inputs reach the vulnerable API before asserting exploitability. [Django advisory](https://www.djangoproject.com/weblog/2025/nov/05/security-releases/).

## Completed work and residual limits

- Argo main runtime source2a20e755, image digest9d07d6eb5fb796354a83cbd03868ba445910e347bbda91c261994d220dcdc3fb: deployed by4bf38125; live Ready main containers reverified. Prior exact-image scans both architectures0critical/0high, with medium/low/unknown findings remaining. Rendering/decryption, Git and reconciliation passed in prior execution. Interactive SSO and sustained observation remain open. Obsolete KSOPS emptyDir mount was removed by guarded live patch after SSA retained it; desired source already removes it.
- Argo ingress and Backoffice tier isolation have prior positive/negative traffic evidence. This read-only review did not repeat those active tests.
- Pages rebuilt/pinned with restored-copy and production content verification;49high without listed fixes remain for disposition.
- Wallabag runtime fixed/pinned, restored-copy login/extraction/export verified; legacy Guzzle/Symfony/OTPHP remain, scan1critical/16high. Feature non-use is not remediation.
- CrowdSec remains a canary; production routes are not protected by it yet. Later stale-cache failure, real WAN identity and alert delivery remain gates.

## Per-batch execution and rollback contract

1. Record current source revision, running platform digest, baseline functionality and data recovery location. Preserve unrelated checkout changes and original PVCs.
2. Pin explicit version and digest in Kustomize; inspect rendered main/init containers. No latest replacement. Validate schema and server admission without printing decrypted manifests.
3. Archive exact-image full scan, including unfixed findings, privately. Record CVE/package/fix, reachability, privileges, compensating controls, owner and review date for each residual critical/high disposition.
4. Run restore, application, authentication and negative authorization/network tests appropriate to the change. Stop for data differences, lost recovery access or failed functionality.
5. Commit/push one coherent change, reconcile, verify actual runtime digest and rerun acceptance checks. Git state or podReady alone is insufficient.
6. For stateless failures revert that GitOps batch and confirm recovery. For schema migrations use the tested restore plan; never blindly downgrade a database image. Do not force-push or delete recovery state.
7. Record evidence and remaining limits in the progress ledger. Schedule sustained observation explicitly if needed; this plan itself does not create monitoring.

For workloads that do not use Kubernetes, set automountServiceAccountToken:false in the pod template; preserve tokens needed by genuine controllers and scope their RBAC. [Kubernetes service-account guidance](https://kubernetes.io/docs/concepts/security/service-accounts/).

Full patching is incomplete. Existing authorization covers upgrades, pins, commits/pushes and necessary PRs; the old invisible form is not a blocker. Backoffice deployment completed after the review. Continue with containment, sensitive application patching and coverage work; the full goal remains open.


## Subsequent Hermes findings

Dashboard ingress restriction deployed and verified by8fc8eb68; callback8645 source restriction and egress remain open. Cluster-admin is explicitly intentional in source and remains a residual risk requiring a management-preserving design. Current pod remains healthy without restart, but ExternalSecrethermes-api-keys hasSecretSyncedError and Argo has a pre-existing Degraded status: investigate credential-store reconciliation before a runtime rollout.

A fresh exact-runtime scan with pinned Trivy0.76.1 recovered previously missing Hermes evidence:37critical/1054high,752critical/high occurrences with listed fixes. Full32MiB report is private. Trivy0.65 failed layer extraction;0.76.1 succeeds, but removed--slow means scanner/operator upgrade requires compatible commands and archiver/ingestion tests. Production scanner is unchanged. See latest ledger for exact digests and evidence.


## Scanner upgrade and credential dependency update

Shared 1Password provider rejects the service account for insufficient access; five ExternalSecrets are affected. Preserve cached Secrets and avoid Hermes restart until access is repaired through the configured encrypted credential path. User clarification is pending; other patch batches continue.

Official Trivy operator0.35.0 still has5fixable high findings. A downstream candidate from upstream main047f1d9 (alreadyGo1.27.3/x-net0.60.0) plus tested0.76flag compatibility is building asrun40286037966, source725e0fe. It is not deployed. Exact scans, CRD/admission checks, emitted commands, private archive recovery, actual report ingestion and metrics are mandatory before rollout. Do not install nonexistent ServiceMonitor CRDs merely to satisfy this chart; existing static Prometheus scraping must be preserved. See latest ledger for outstanding field-ownership issues and the pending upstream issue form.


## Registry transfer failure resolved

Fresh0.76.1 scans reproduced the earlier extraction failure; Envoy evidence proved large image downloads were cut off at15seconds. Registry-only600second request/backend deadlines are deployed by a17492c1, with explicit API defaults in8f6ff960. The same403815069-byte layer now completes beyond15seconds and the route isSynced. Do not attribute the old failure solely to scanner version.

Live trivy-archive captured the resulting full Hermes report, verified checksum/counts/current runtime digest, and retained recoverability after scan-job deletion; metricsHTTP200 and archive_errors0. New-operator CRD ingestion and candidate image scans remain outstanding. Build40286037966 is still running; production operator remains unchanged.
