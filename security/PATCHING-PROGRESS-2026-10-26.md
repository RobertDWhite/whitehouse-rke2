# Full patching execution — October 26, 2026

Goal remains full execution and verification of PATCH-PLAN-2026-10-25.md, not merely an Argo upgrade.

## In progress: Argo CD

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
