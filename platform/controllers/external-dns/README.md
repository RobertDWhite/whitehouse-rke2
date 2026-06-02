# external-dns wiring (Cloudflare + Pi-hole)

This stack now runs two `external-dns` deployments:

- `external-dns` for public DNS in Cloudflare (existing behavior).
- `external-dns-pihole*` for internal DNS in Pi-hole.

Pi-hole fanout deployments in this repo:

- `external-dns-pihole` -> primary Pi-hole (from secret `pihole-credentials.server`)
- `external-dns-pihole-b` -> `https://10.100.0.21`
- `external-dns-pihole-c` -> `https://10.99.5.2`

Each deployment watches ingress and writes the same A records to its Pi-hole endpoint. This makes DNS updates automatic during Kubernetes deploys without manual Pi-hole sync.

## Pi-hole credentials

Set your Pi-hole API values in `pihole-credentials-secret.yaml`:

- `server`: Pi-hole base URL (example `https://10.99.5.2`)
- `password`: Pi-hole password or app password

You can replace this plain secret with a SOPS/ExternalSecret workflow if preferred.

## How records are created

`external-dns-pihole` watches `Ingress` resources with ingress class `nginx` and creates/updates records for:

- `*.white.fm`
- `*.w3rdw.radio`
- `*.whitematter.tech`
- `*.internal`

For this cluster, all Pi-hole deployments force records to ingress VIP `10.99.5.110` via:

- `--default-targets=10.99.5.110`
- `--force-default-targets`

## .internal example

Use a dedicated internal hostname on an ingress:

```yaml
metadata:
  annotations:
    cert-manager.io/cluster-issuer: internal-ca
spec:
  ingressClassName: nginx
  rules:
    - host: app.internal
      http: ...
```

## cert-manager note

Let's Encrypt will not issue trusted certificates for private suffixes like `.internal`.

- For publicly trusted certs: use a public domain you control (split-horizon DNS via Pi-hole).
- For `.internal`: use an internal CA issuer in cert-manager (for example CA/Vault/step-ca) and trust that CA on clients.
