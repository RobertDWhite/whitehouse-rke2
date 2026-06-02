# Technitium DNS

Cluster-hosted recursive/forwarding DNS with web UI, blocklists, DoH/DoT/DNSCrypt
upstreams, and authoritative zones for the `internal.*` TLDs. Replacement path
for Pi-hole; runs alongside it during cutover.

## Authentication

Two layers, by design:

- **Native OIDC SSO with Authentik.** Technitium 15.1 has built-in OIDC at
  `/api/admin/sso/{get,set}`. An Authentik OAuth2/OpenID Provider is
  created (slug `technitium-sso`) with both hostnames as redirect URIs;
  the issuer + client ID + client secret are stored in
  `30-secret.sops.yaml` and applied to Technitium via API. The UI shows
  an "SSO Login" button that redirects to Authentik. Authentik's
  `authentik Admins` group is mapped to Technitium's `Administrators`.
- **Local fallback** is the Technitium admin password. Recover with
  `sops -d technitium/30-secret.sops.yaml | awk -F': ' '/admin-password/ {print $2}'`
  and reach the UI via `kubectl port-forward technitium-0 5380:5380`. Use
  this if Authentik is unavailable.

To re-create the SSO config (after disaster, or to bootstrap a new install):
see `bin/setup-authentik-sso.md`.

## How records flow

```
35-zones-secret.sops.yaml   ─┐
 (sops-encrypted; edit       │
  with `sops` interactively) │
                             ├─► ksops decrypts → Secret technitium-zones
                             │                              │
                             │                              ▼
                             │              /zones in every Technitium pod
                             │                              │
                             │              sidecar `zone-importer` watches mtime
                             │                              │
                             └────────► Technitium HTTP API on localhost:5380
                                                            │
                                                            ▼
                                                  Authoritative zones
```

The encrypted Secret is the source of truth. Edit via `sops`, push, ArgoCD
applies the new Secret, the sidecar in each pod hot-reloads within ~60s.

## Layout

| File | Purpose |
| --- | --- |
| `bin/pull-from-pihole.py` | Local script — re-seed zones from Pi-hole, writes the encrypted Secret |
| `35-zones-secret.sops.yaml` | sops-encrypted Secret with all zone YAMLs as keys |
| `30-secret.sops.yaml` | sops-encrypted credentials (Technitium admin + Pi-hole API) |
| `10-statefulset.yaml` | 4-replica StatefulSet + `zone-importer` sidecar in each pod |
| `20-service-dns.yaml` | LoadBalancer (MetalLB IPs `10.99.5.50` + `10.99.5.51`) for UDP/TCP 53 |
| `21-service-web.yaml` | ClusterIP for the 5380 admin UI |
| `22-service-headless.yaml` | Per-pod DNS for emergency CronJob targeting |
| `40-httproute-internal.yaml` | `technitium.internal.white.fm` + `dns.internal.white.fm` |
| `50-configmap-import-script.yaml` | Sidecar Python script (watch + import) |
| `90-emergency-pihole-sync.yaml` | Disabled by default — manual Pi-hole → Technitium re-sync |

## First-time setup

The credentials Secret and zones Secret have already been generated and
committed. To verify:

```bash
sops -d technitium/30-secret.sops.yaml      | grep -E "(admin|pihole)-password" | sed 's/:.*$/: <set>/'
sops -d technitium/35-zones-secret.sops.yaml | grep -E "^    internal\." | sed 's/:.*$/: <set>/'
```

Recover the Technitium admin password whenever you need it:
```bash
sops -d technitium/30-secret.sops.yaml | awk -F': ' '/admin-password/ {print $2}'
```

(Optional) label your 10G nodes so the soft affinity does something:
```bash
kubectl label node <node-X> network=10g
```

## Updating records (the GitOps way)

Edit the Secret directly with sops — your `$EDITOR` opens the decrypted YAML;
sops re-encrypts on save:

```bash
sops technitium/35-zones-secret.sops.yaml
git commit -am "technitium: add cool-thing.internal.white.fm"
git push
```

Within ~60s of ArgoCD syncing the Secret, every Technitium pod picks up the
change. Confirm:

```bash
kubectl -n technitium logs technitium-0 -c zone-importer --tail=20
dig @10.99.5.50 cool-thing.internal.white.fm
dig @10.99.5.51 cool-thing.internal.white.fm
```

## Re-seeding from Pi-hole

If Pi-hole has new records you want to bring over:

```bash
PIHOLE_PASSWORD=$(sops -d external-dns/pihole-credentials-secret.sops.yaml \
                    | awk -F': ' '/^    password:/ {print $2; exit}') \
  ./technitium/bin/pull-from-pihole.py
git diff technitium/35-zones-secret.sops.yaml   # diff is encrypted — see below
```

The script overwrites the Secret entirely — manual edits to records that exist
in Pi-hole will be replaced. Records you've only added to Technitium (not
Pi-hole) are wiped. Run intentionally.

To get a meaningful diff against the previous version, compare decrypted
content:

```bash
git show HEAD:technitium/35-zones-secret.sops.yaml | sops -d /dev/stdin \
  | diff - <(sops -d technitium/35-zones-secret.sops.yaml)
```

## High availability

The DNS Service has two MetalLB VIPs (`10.99.5.50` + `10.99.5.51`), both
backed by all 4 pods via kube-proxy. Configure your router to use both:
- Primary DNS: `10.99.5.50`
- Secondary DNS: `10.99.5.51`

Both VIPs ultimately serve the same pods, so this is mostly a comfort feature —
real resilience comes from the 4-pod spread + MetalLB L2 failover (sub-10s when
an elected node dies). Add a public secondary in your router (`1.1.1.1` /
`9.9.9.9`) if you want survivable DNS during a full Technitium outage.

## Upstream resolvers + blocklists

Technitium 15.1 doesn't natively support ODoH or DNSCrypt — only plain UDP/TCP,
DoT, DoH, and DoQ. To get ODoH, an in-cluster `dnscrypt-proxy` Deployment
(see `60..62-*`) provides the relay+target dance and Technitium forwards
to it on plain UDP/53.

Topology:
```
client → Technitium pod → dnscrypt-proxy Service (UDP/53)
                              │
                              ▼
                          dnscrypt-proxy Deployment (2 replicas)
                              │
                  ODoH relay → ODoH target → answer
```

Bootstrap defaults applied **once**, on first boot of a fresh Technitium pod:
- Forwarder: `dnscrypt-proxy.technitium.svc.cluster.local` (UDP)
- DNSSEC validation: on
- Blocklist: `https://big.oisd.nl/`

After the first boot the UI is the source of truth — sidecar reloads don't
overwrite forwarder/blocklist settings.

The dnscrypt-proxy config (`60-configmap-dnscrypt-proxy.yaml`) lists 4 ODoH
targets (cloudflare, crypto.sx, koki-ams, ibksturm) and 4 relays. Routes are
randomized per query so each query goes target↔relay independently.

Bootstrap defaults live as env vars on the `zone-importer` container in
`10-statefulset.yaml` — only edit those if you want fresh pods to start
out with a different forwarder.

## Emergency Pi-hole sync

Disabled by default. To run a one-off Pi-hole → Technitium copy without
committing zone changes:

```bash
# 1. Uncomment the resource line in kustomization.yaml:
#    - 90-emergency-pihole-sync.yaml
# 2. Commit + push; ArgoCD applies the suspended CronJob + ConfigMap.
# 3. Trigger:
kubectl -n technitium create job --from=cronjob/pihole-sync sync-now
kubectl -n technitium logs -l job-name=sync-now -f
# 4. Re-comment the line and push to prune the resources.
```

## Cutover from Pi-hole

1. Stand up Technitium (this directory).
2. Verify both VIPs respond:
   ```bash
   dig @10.99.5.50 some.internal.white.fm
   dig @10.99.5.51 some.internal.white.fm
   dig @10.99.5.50 anthropic.com    # confirms recursion through Quad9/Cloudflare
   ```
3. Flip your router's DHCP DNS option to `10.99.5.50` (primary) +
   `10.99.5.51` (secondary).
4. Once happy, remove the Pi-hole `external-dns` deployments
   (`external-dns/deployment-pihole{,-b,-c}.yaml`) and shut down the Pi-holes.
5. Optionally, point CoreDNS upstream at `10.99.5.50` so pods get the
   blocklists too.

## Known caveats

- Sidecar imports *additively* with `overwrite=true`. Removing a record from a
  zone in the Secret will not delete it from Technitium's existing zone —
  delete via UI when needed, or extend `import.py` to compare-and-prune.
- The MetalLB Service uses `externalTrafficPolicy: Cluster`, so Technitium
  query logs show node IPs rather than real client IPs.
- No Prometheus scrape yet — Technitium has no native `/metrics`. Add the
  community exporter later if needed.
