# Fleet MDM — Initial Setup

This is a one-time bring-up checklist. Run these steps in order before pushing
the `fleet/` directory to git for the first time.

## 1. Generate secrets

Three Secrets need real values before ArgoCD will sync successfully. The file
`22-secret.sops.yaml` currently contains plaintext placeholders. Generate
values and fill them in, **then encrypt with sops before commit**.

```sh
# Generate the values
MYSQL_ROOT_PW=$(openssl rand -base64 32 | tr -d '/+=' | head -c 32)
MYSQL_FLEET_PW=$(openssl rand -base64 32 | tr -d '/+=' | head -c 32)
REDIS_PW=$(openssl rand -base64 32 | tr -d '/+=' | head -c 32)
FLEET_KEY=$(openssl rand -hex 32)

echo "MYSQL_ROOT_PW=${MYSQL_ROOT_PW}"
echo "MYSQL_FLEET_PW=${MYSQL_FLEET_PW}"
echo "REDIS_PW=${REDIS_PW}"
echo "FLEET_KEY=${FLEET_KEY}"
```

**Save `FLEET_KEY` to your password manager immediately.** Lose it and every
enrolled MDM device becomes unrecoverable — MySQL backups are encrypted with
this key.

Open `fleet/22-secret.sops.yaml` and replace the four `REPLACE_…` placeholders
with the generated values. Then encrypt in place:

```sh
cd fleet
sops -e -i 22-secret.sops.yaml
```

Verify it encrypted (the file should now contain `ENC[AES256_GCM,…]` ciphertext
under each `stringData` key and a `sops:` footer with the age recipient).

## 2. Sanity-check the kustomize build

```sh
cd fleet
kubectl kustomize --enable-alpha-plugins .
```

Should render without errors. If Helm complains it can't reach the chart repo,
`helm repo add fleet https://fleetdm.github.io/fleet/charts && helm repo update`
first.

## 3. Commit and push

Files to commit:

```
fleet/**
storage/fleet/**
storage/kustomization.yaml          # added: - fleet
argo-cd/applications/fleet-app.yaml
velero/schedules/daily.yaml         # added: - fleet
observability/02-config-prometheus.yaml  # added scrape jobs
```

ArgoCD picks up `fleet-app.yaml` from app-of-apps, creates the namespace,
applies everything, syncs.

## 4. Watch the rollout

```sh
kubectl -n fleet get pods -w
```

Expected order: `mysql` Ready → Fleet helm pre-install migrations Job
Completed → `fleet-*` (2 replicas) Ready → `valkey` Ready.

## 5. First-run web setup

Browse to **https://fleet.white.fm**. You'll get Fleet's first-run wizard:

- Create the initial admin user (email + password). Save in your password
  manager.
- Org name: `Whitehouse`.
- Server URL: pre-populated as `https://fleet.white.fm`.

After login, Settings → Organization settings → confirm `Fleet server URL` is
`https://fleet.white.fm`.

## 6. Apple MDM setup

Once Fleet is running, enable Apple MDM end-to-end. You need:

- A Managed Apple ID (free; create at appleid.apple.com under a domain you own —
  don't use your personal Apple ID).
- Apple Business Manager enrollment (free; sign up at business.apple.com,
  needs a D-U-N-S number, takes ~24h to approve).

### 6a. Generate the APNs CSR

```sh
# fleetctl can generate the CSR locally (no Fleet server interaction needed)
fleetctl generate mdm-apple --email <managed-apple-id> --org "Whitehouse"
# Outputs: fleet-mdm-apple-apns.csr + .key in the working directory
```

Upload `fleet-mdm-apple-apns.csr` to https://identity.apple.com/pushcert/ →
"Create a Certificate" → select Vendor → upload CSR → download the resulting
`.pem`.

In Fleet UI: Settings → Integrations → MDM → Apple → upload the `.pem` and
paste the contents of the `.key` file.

**The APNs cert expires yearly.** Renew at the same URL before expiry —
losing it means re-enrolling every Mac.

### 6b. Apple Business Manager (zero-touch enrollment)

In ABM: Preferences → MDM Servers → Add MDM Server → "Whitehouse Fleet" →
download the ABM public key. In Fleet UI, generate an ABM keypair, upload the
ABM public key, then upload the resulting token back to ABM.

Once linked, any Mac you buy via ABM auto-enrolls into Fleet on first boot.

## 7. Windows MDM setup

Fleet UI: Settings → Integrations → MDM → Windows → Connect → paste your Entra
tenant ID. (Manual enrollment without Entra also works for testing — just point
Windows at `https://fleet.white.fm` from Settings → Access work or school.)

## 8. First device enrollment + backup verification

Enroll a test device:

```sh
fleetctl package --type=pkg --fleet-url=https://fleet.white.fm --enroll-secret=<from UI>
# Or .deb / .msi / .rpm
```

Install on a Mac/Windows/Linux host. Host appears in Fleet UI within 30s.

Then verify backups work:

```sh
# Manual logical-dump test run
kubectl -n fleet create job --from=cronjob/fleet-mysqldump dump-test
kubectl -n fleet logs job/dump-test --follow
kubectl -n fleet exec deploy/mysql -- ls -lh /tmp 2>/dev/null  # just to confirm pod is alive
kubectl -n fleet exec -it $(kubectl -n fleet get pod -l app=mysql -o name | head -1) -- \
  sh -c "ls -lh /backups 2>/dev/null || echo 'no /backups mount on mysql pod (expected)'"

# Check the dump landed on the dumps PVC
kubectl -n fleet run -it --rm peek --image=busybox --restart=Never --overrides='
{"spec":{"containers":[{"name":"peek","image":"busybox","command":["sh","-c","ls -lh /b && sleep 5"],"volumeMounts":[{"name":"b","mountPath":"/b"}]}],"volumes":[{"name":"b","persistentVolumeClaim":{"claimName":"fleet-mysql-dumps"}}]}}' -- sh

# Manual Velero backup test
velero backup create fleet-test --include-namespaces fleet --wait
velero backup describe fleet-test --details | grep -i pvc
```

If all three succeed, you're done.

---

## Upgrade gotchas

### Bumping the chart version (`fleet/kustomization.yaml` `helmCharts[0].version`)

The chart includes a `fleet-migration` Job marked as a Helm `pre-install,pre-upgrade` hook. Because we render via `kustomize helmCharts:` (not `helm install`), ArgoCD treats it as a normal Kubernetes resource and does **not** re-run it on subsequent syncs. If a chart bump ships a schema migration, you must delete the existing Job before pushing:

```sh
kubectl -n fleet delete job fleet-migration
git push   # ArgoCD recreates and re-runs it
```

### Bumping the Fleet image tag (`images:` block, `fleetdm/fleet`)

Patch-version bumps usually have no migration; minor/major bumps usually do. Check the Fleet release notes. When in doubt, delete the migration Job before pushing.

### Renovate PRs

Renovate watches `helmCharts[].version` (chart) and the `images:` block (`fleetdm/fleet`, `mysql`, `valkey/valkey`, `prom/mysqld-exporter`, `busybox`). Review chart bumps for migration impact before merging.

## Operations

- Logs: `kubectl -n fleet logs deploy/fleet -f`
- MySQL shell: `kubectl -n fleet exec -it deploy/mysql -- mysql -uroot -p"$ROOT_PW" fleet`
- Trigger a backup now: `kubectl -n fleet create job --from=cronjob/fleet-mysqldump on-demand-$(date +%s)`
- Restore: see `RESTORE.md`

