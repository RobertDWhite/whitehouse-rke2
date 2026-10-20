# Fleet GitOps (Argo-driven)

Declarative config for the 8 fleets on fleet.white.fm, exported from the live
database on 2026-08-06. Argo CD deploys everything here; no manual apply step.

## How it flows

1. `fleets/*.yml` + `lib/**` are packaged into ConfigMaps by the
   `configMapGenerator` entries in `../kustomization.yaml` (content-hashed, so
   any edit rolls the Job/CronJob references).
2. Secrets (API token, Wi-Fi PSKs, watch enroll secret) live in
   `../23-secret-gitops.sops.yaml` (KSOPS/age). Edit with
   `sops apps/misc/fleet/23-secret-gitops.sops.yaml`.
3. `../46-job-gitops-apply.yaml` — Argo PostSync hook: `fleetctl gitops` runs on
   every sync of this app, so merged config changes apply immediately.
4. `../45-cronjob-gitops.yaml` — nightly (03:30) run: refreshes unpinned
   Fleet-maintained / App Store app versions to latest without a repo change.

Both jobs use `registry.internal.white.fm/fleetctl-self-hosted` (kustomize
`images:` pin), a fleetctl-only image built from the fleet-self-hosted fork so
it understands the fork's tvOS/watchOS keys. Upstream fleetctl rejects them, and
the server image does not ship fleetctl. Source: branch `fleetctl/server-compat`
in RobertDWhite/fleet-self-hosted. It is the codex/apple-fleet-workspace tip plus
one patch: leave out `deadline_days`, which the deployed server rejects. Rebuild
with `CGO_ENABLED=0 GOOS=linux GOARCH=<arch> go build -o fleetctl-<arch>
./cmd/fleetctl` for amd64 and arm64, then use `docker buildx --push` with an
alpine Dockerfile that copies `fleetctl-${TARGETARCH}` to /usr/bin/fleetctl.
fleetctl reads the server address and token only from its config file, so each
job runs `fleetctl config set` from `FLEET_URL`/`FLEET_API_TOKEN` first.

Software is declared as unpinned `fleet_maintained_apps`, matching how it is
installed in Fleet. Custom `packages:` can't express Fleet-maintained `.dmg`
apps. Enroll secrets are UI-owned: Fleet's GitOps exception for `secrets` is
on, so the fleet files must not contain a `secrets:` key.

Manual run: `kubectl create job -n fleet --from=cronjob/fleet-gitops gitops-manual`

## Scope — what stays UI-owned

No `default.yml` is applied and `--delete-other-fleets` is never passed, so org
settings (SSO, SMTP, MDM/ABM/VPP config, global enroll secret), Unassigned
hosts, users, and any fleets not listed here are untouched by GitOps. The
Fleet-generated profiles (caroot, fleetd config, FileVault) are never declared
here — the server emits those itself.

For the 8 declared fleets the YAML is authoritative: profiles, policies,
software, enroll secrets, agent options, features. UI edits to those sections
revert on the next run.

## Secret material

- Wi-Fi PSKs are `$FLEET_SECRET_*` variables in the mobileconfigs; the watch
  DDM declaration's OTA URL embeds the **global** enroll secret the same way
  (stored URL-encoded). fleetctl uploads `FLEET_SECRET_*` values from its
  environment to the server, which substitutes them at profile delivery.
- `FLEET_API_TOKEN` starts as a placeholder: create an API-only user with the
  GitOps role (Settings > Users), then update the sops file.

## First sync checklist

1. Set `FLEET_API_TOKEN` in `23-secret-gitops.sops.yaml`.
2. Merge; let Argo sync. Watch `kubectl logs -n fleet job/fleet-gitops-apply`.
3. Expect first-run churn: the two custom packages re-download from URL
   (1Password 8.12.30, balenaEtcher 2.1.6) and Wi-Fi profiles re-issue after
   secret-variable substitution (delivered bytes identical).

## Fleet-maintained app auto-updates (macOS)

Add unpinned `fleet_maintained_apps` + `type: patch` policies with
`install_software: true` to `fleets/family-macos.yml`; the nightly CronJob then
tracks Fleet's catalog. Pattern: `it-and-security/lib/macos/policies/
patch-fleet-maintained-apps.yml` in the fleet repo.
