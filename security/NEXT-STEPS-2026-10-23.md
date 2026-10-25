# Security next-step investigation

Read-only cluster investigation on 2026-10-23, following HANDOVER-2026-10-23.md. No deployment, Git commit, push, or upstream PR change performed. Existing untracked handover preserved. Fetched origin/main before investigation.

## Refreshed evidence

Snapshot taken at 19:54:58 UTC using `security/tools/posture_snapshot.py`, saved separately at `/tmp/cluster-posture-current.json` (temporary evidence; original baseline unchanged):

- 454 running pods and 257 distinct running main-container digests, unchanged.
- 196 vulnerability reports versus 182; 64 matched digests versus 61. Exact digest coverage is 24.9%, still a lower bound because index/platform digests can differ. Init and ephemeral containers remain excluded.
- 33 running namespaces lack NetworkPolicies; presence of a policy does not establish isolation.
- Falco DaemonSet ready 6/6. This check did not repeat detection or delivery tests.
- MISP backend retains `insecureSkipVerify: false`, explicit SNI, and System CA verification.
- FreshRSS is Synced/Degraded; MISP and Wallabag Synced/Healthy. Envoy routes OutOfSync/Healthy. These are Argo statuses, not repeat HTTP/application tests.
- All seven CrowdSec pods still use v1.6.4. Bouncer registry contains only old dashboard consumers, with the most recent pull in August; no active Envoy bouncer observed.

## First action: activate the Trivy TTL already configured

The handover's TTL mitigation is configured but is not active in the existing operator process:

- Live `trivy-operator-config` has `OPERATOR_SCAN_JOB_TTL: 1h`.
- Deployment consumes that ConfigMap through `envFrom`.
- Operator pod started 2026-10-21, before this configuration change.
- Targeted `printenv OPERATOR_SCAN_JOB_TTL` inside the operator returned an empty value.
- All five current completed scan jobs have no `spec.ttlSecondsAfterFinished`.
- Recent operator logs continue to report roughly 2.167 MB results rejected against the 2,097,152-byte message limit.

Recommended implementation sequence:

1. Preserve failed scan output in restricted storage before any cleanup, since scans can include secret findings. Do not put raw reports in Git or tool output.
2. Roll the operator to reload its ConfigMap environment; make future configuration changes trigger a rollout through a supported chart mechanism or repository reloader convention.
3. Apply a bounded TTL to already completed jobs after preserving evidence. Restarting the operator alone does not retrofit TTLs on existing Jobs, and these jobs can still occupy the scan slots.
4. Verify process TTL equals `1h`, newly created jobs have TTL 3600, scan slots recover, and ingestion/coverage improves.

This resolves queue retention, not the oversized-report storage failure. Do not treat increased report count alone as completion.

## Second action: retain oversized findings and make gaps observable

Prototype on one known oversized image before changing the cluster-wide report backend. Keep current CRD reporting while evaluating a separate durable full-result path and summary/coverage exporter. Required data: image/platform digest, scan time, scanner/database versions, scan settings, result location/checksum, and explicit failure status. Include init/ephemeral images in a separate inventory so baseline comparisons remain meaningful.

Current upstream `main` still returns empty report objects when alternate storage is enabled. It also has deferred completed-job cleanup, but that is source inspection, not verification of a released compatible upgrade. Neither behavior establishes durable findings plus compatible CRD metrics. Source: https://github.com/aquasecurity/trivy-operator/blob/main/pkg/vulnerabilityreport/controller/scanjob.go

Acceptance: oversized details survive job deletion and operator restart; every current image has a recent result or an explicit failure; missing/stale scans alert; metrics continue to represent the chosen source. Test namespace-safe storage keys, retention, backup, and access restrictions. Expand unfixed/low visibility only after measuring capacity.

## Third action: CrowdSec upgrade, then a one-route Envoy pilot

Upstream latest release API reports v1.8.1. Treat it as a candidate requiring chart compatibility, image provenance/digest and architecture checks, and a rescan; a newer tag does not prove all findings fixed. Back up database/configuration and validate recovery before migration. Verify agent registration, parsers and LAPI after upgrading, before adding enforcement.

CrowdSec documents `kdwils/envoy-proxy-crowdsec-bouncer`, explicitly a community component, using Envoy external authorization. Deploy the bouncer separately and manage a single HTTPRoute SecurityPolicy explicitly; avoid the quickstart's gateway-wide policy setting for the pilot. Inspect existing route policies before combining authorization and extAuth so source restrictions are preserved.

Live Envoy service has `externalTrafficPolicy: Local` and VIP `10.99.5.110`. The tracked EnvoyProxy comments mention Local but its service block does not explicitly set it. Determine whether the chart/controller default supplies it before making that guarantee durable. Local source handling alone does not validate upstream proxies or forwarded headers.

Pilot acceptance: Envoy acquisition parses the real client IP; bouncer evaluates that same IP; spoofed forwarding headers cannot bypass a ban or implicate another client; legitimate traffic passes; a controlled ban blocks only its intended client and is removed afterward; metrics/alerts observe the test. Exercise bouncer/LAPI failure and recovery with explicit timeout and fail-open/fail-closed choices. Keep management access independent. Add AppSec only after basic remediation is proven.

Sources:
- https://docs.crowdsec.net/u/bouncers/envoy/
- https://github.com/crowdsecurity/crowdsec/releases/tag/v1.8.1

## Subsequent work

Continue Wallabag patched-runtime compatibility work and effective trust-store remediation. Prioritize direct gateway-bypass services and privileged agent interfaces for reachability/authentication review. Separate storage/database upgrades from application patching and require restore testing. Address Argo drift and degraded monitoring because they can conceal or undo security changes. Independent WAN denial, historical compromise assessment, database restoration, and end-to-end alert delivery remain unverified.

Upstream Wallabag/MISP PRs were background references in this pass; their status was not refreshed and no work was performed on them.
