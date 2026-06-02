# Fleet MDM — Restore Runbook

Three backup tiers, in order of preference:

| Tier | Source | RPO | Restore time |
|------|--------|-----|--------------|
| 1 | mysqldump on `fleet-mysql-dumps` PVC | ≤6h | 5–10 min |
| 2 | Velero CSI snapshot of `fleet-mysql` PVC | ≤24h | 10–20 min |
| 3 | Off-cluster: SOPS secret in git + dump PVC also caught by Velero | ≤24h | 30+ min |

The **`server-private-key`** in `fleet/22-secret.sops.yaml` is required to
decrypt MDM-sensitive columns. If the cluster is gone but the git repo is
intact, you have the key. **If you lose both the cluster and the password
manager copy AND the git repo, MDM data is unrecoverable.**

---

## Scenario A — Single corrupted table or accidental delete

Use Tier 1 (logical dump). Doesn't require taking Fleet offline if you're
careful with selective restore.

```sh
# 1. List available dumps
kubectl -n fleet run -it --rm peek --image=mysql:8.0 --restart=Never \
  --overrides='{"spec":{"containers":[{"name":"peek","image":"mysql:8.0","command":["sh","-c","ls -lh /b && sleep 60"],"volumeMounts":[{"name":"b","mountPath":"/b","readOnly":true}]}],"volumes":[{"name":"b","persistentVolumeClaim":{"claimName":"fleet-mysql-dumps"}}]}}'

# 2. Extract the specific table you want from a chosen dump
DUMP=fleet-20260510-030001.sql.gz   # pick one
kubectl -n fleet cp <peek-pod>:/b/$DUMP /tmp/$DUMP
gunzip -c /tmp/$DUMP | sed -n '/CREATE TABLE `hosts`/,/UNLOCK TABLES/p' > /tmp/hosts.sql

# 3. Replay into Fleet's MySQL
kubectl -n fleet exec -it deploy/mysql -- mysql -uroot -p<root-pw> fleet < /tmp/hosts.sql
```

## Scenario B — Full MySQL corruption / cluster intact

Use Tier 1 first; Tier 2 if dump is missing.

```sh
# 1. Scale Fleet to 0 so it stops writing
kubectl -n fleet scale deploy fleet --replicas=0

# 2. Take a snapshot of the current broken state for forensics
velero backup create fleet-pre-restore --include-namespaces fleet --wait

# 3. Empty the existing database (KEEP the user/grants)
kubectl -n fleet exec -it deploy/mysql -- mysql -uroot -p<root-pw> -e \
  "DROP DATABASE fleet; CREATE DATABASE fleet CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"

# 4. Restore from the latest good dump
DUMP=fleet-YYYYMMDD-HHMMSS.sql.gz
kubectl -n fleet exec -i deploy/mysql -- sh -c "gunzip | mysql -uroot -p<root-pw> fleet" < <(kubectl exec -n fleet $(kubectl get pod -n fleet -l app=mysql -o name|head -1) -- cat /backups/$DUMP)
# (the dumps PVC isn't mounted in the mysql pod — easier path:)
#   kubectl run a sidecar with both PVCs mounted and pipe gunzip → mysql there.

# 5. Bring Fleet back up
kubectl -n fleet scale deploy fleet --replicas=2

# 6. Validate
curl -sf https://fleet.white.fm/healthz   # expect "ok"
# Browse Fleet UI → Hosts page should show pre-restore host counts
```

## Scenario C — Cluster-level disaster, restoring elsewhere

Tier 3. You need:

1. The git repo (gives you the manifests + SOPS-encrypted secrets).
2. The age private key for SOPS (the one `argo-cd/secret-sops-age.sops.yaml`
   references — you should have this in your password manager or a separate
   secure backup).
3. Either a Velero snapshot you can restore (preferred) or the latest dump
   `.sql.gz` exfiltrated before disaster.

```sh
# Bring up a fresh cluster, apply argo-cd manifests
# Once ArgoCD syncs the fleet namespace, it'll create empty MySQL.

# 1. Restore from Velero (preferred — gives you the binary PVC state)
velero restore create fleet-restore --from-backup <backup-name> \
  --include-namespaces fleet --wait

# 2. OR if Velero is unavailable, restore from the latest .sql.gz dump:
#    Copy the dump file onto the new cluster's fleet-mysql-dumps PVC, then
#    follow Scenario B step 4.

# 3. Verify the server-private-key secret was restored from SOPS — the
#    cluster will look like it's running but MDM commands will fail
#    decryption otherwise.
kubectl -n fleet get secret fleet -o jsonpath='{.data.server-private-key}' | base64 -d | wc -c
# Should print 64 (32 bytes hex).
```

## Scenario D — APNs cert expired or lost

The APNs cert is renewable but enrolled Macs need to be re-enrolled if you
change to a different cert (different "topic"). Renewal of the SAME cert is
the only way to keep enrollments alive.

- Renewal: https://identity.apple.com/pushcert/ → "Renew" on the existing
  cert. Upload new CSR, get new .pem, upload to Fleet UI.
- If lost entirely (no renewal possible): all Macs must be wiped and
  re-enrolled. This is why the APNs CSR private key is also worth keeping in
  a password vault.

## Drill schedule

Run **Scenario B** end-to-end on a test cluster (or against a scratch
database) once a quarter. Record outcome in this file. Last drill:

- _(none yet — first drill due 90 days after deploy)_
