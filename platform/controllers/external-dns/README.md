# ExternalDNS wiring (Cloudflare)

This stack runs two ExternalDNS controllers, both on v0.22:

- `external-dns` watches Gateway API `HTTPRoute` resources and publishes public
  Cloudflare CNAMEs to the Cloudflare Tunnel.
- `external-dns-records` watches opt-in `DNSEndpoint` resources for exceptional
  records that are not naturally represented by an HTTPRoute.

The controllers use separate TXT owner IDs and must not manage the same DNS
name/type pair:

- `whitehouse-rke2` — public HTTPRoute records
- `whitehouse-rke2-records` — declarative DNSEndpoint records

## Public HTTPRoute records

The public controller manages only CNAME records in `white.fm`, `w3rdw.radio`,
and `whitematter.tech`. It forces them to the Cloudflare Tunnel target and
enables Cloudflare proxying. Internal domains and the nested `pages.white.fm`
zone are excluded.

ExternalDNS v0.22 uses the GA annotation prefix:

```yaml
external-dns.kubernetes.io/controller: skip
```

Use that annotation for routes whose DNS is owned by another system. For
example, `plex.white.fm` is owned by UniFi DDNS and must remain a DNS-only A
record rather than becoming a tunnel CNAME.

## Exceptional records with DNSEndpoint

The `DNSEndpoint` CRD is installed by this Kustomization. Create a namespaced
resource with the opt-in label to publish a record:

```yaml
apiVersion: externaldns.k8s.io/v1alpha1
kind: DNSEndpoint
metadata:
  name: example-alias
  namespace: external-services
  labels:
    externaldns.white.fm/managed: "true"
spec:
  endpoints:
    - dnsName: alias.white.fm
      recordType: CNAME
      recordTTL: 300
      targets:
        - canonical.example.net.
```

The records controller supports A, AAAA, CNAME, MX, NS, SRV, and TXT records.
Do not declare a name/type already managed by an HTTPRoute or by an external
DDNS system.

## Observability

Both controllers expose `/metrics` on port `7979`. Prometheus scrapes them via
the `external-dns-metrics` and `external-dns-records-metrics` Services. Alerts
cover stale syncs, consecutive reconciliation errors, source failures, TXT
registry failures, and ownership mismatches.

Both controllers emit `RecordError` Kubernetes Events on the affected source
resource, making failures visible with `kubectl describe httproute` or
`kubectl describe dnsendpoint`.
