# condo

[Condo](https://github.com/open-condo-software/condo) — open-source property
management platform (tickets, residents, properties, billing, marketplace).

- `condo.internal.white.fm` — internal, via the Envoy Gateway
- `condo.white.fm` — public, via the cloudflared tunnel, **no Authentik in front**

Condo owns its own multi-tenant auth and serves a mobile/GraphQL API, so the
public host goes straight to the app. Fronting it with the Authentik outpost
would break resident logins and every API client.

## Image

Upstream publishes no usable release image — the only tags on
`ghcr.io/open-condo-software/condo/condo` are werf build-cache blobs from 2023.
The image is built from [RobertDWhite/condo](https://github.com/RobertDWhite/condo),
a fork of upstream, by its `homelab image` workflow:

```bash
gh workflow run homelab-image.yml --repo RobertDWhite/condo --ref homelab/v5.23.0 -f ref=homelab/v5.23.0 -f tag=v5.23.0
```

The fork carries two deviations from upstream, both on the `homelab/*` branches:

- a `BUILD_CMD` build arg, so the build is filtered to `@app/condo` and
  `@app/address-service` instead of the whole monorepo
- `yarn install` without `--immutable` — the private submodule workspaces
  (`apps/callcenter`, `apps/eps`, …) are absent in a public fork, so the
  lockfile legitimately changes

To pick up a new upstream release, branch `homelab/vX.Y.Z` off the upstream tag,
re-apply those two Dockerfile edits, run the workflow, and bump `newTag` in
`kustomization.yaml`.

## Layout

One image, three roles:

| Deployment        | Command                                     |
|-------------------|---------------------------------------------|
| `condo`           | `yarn workspace @app/condo start`           |
| `condo-worker`    | `yarn workspace @app/condo worker`          |
| `address-service` | `yarn workspace @app/address-service start` |

Postgres hosts two databases — `condo` and `address_service` (the second is
created by the `postgres-initdb` ConfigMap on first boot). Redis db 0 is condo,
db 1 is address-service. Migrations run as an initContainer on the `condo` and
`address-service` Deployments; `kmigrator` is a no-op once the schema is current.

Uploads and generated exports live on the `condo-media` RWX PVC
(`platform/storage/storage/condo/`) mounted at `/app/media`.

## Bootstrap

Migrations create the schema but no users. After the first successful sync,
create an admin:

```bash
kubectl -n condo exec deploy/condo -- sh -c 'cd /app/apps/condo && node bin/create-user.js you@white.fm '"'"'{"isAdmin":true,"password":"<pick-a-strong-one>"}'"'"''
```

The `cd` matters: these scripts call `path.resolve('./index.js')`, so running
them from the image's `/app` WORKDIR fails with `MODULE_NOT_FOUND`.

The script prints the created user as JSON. It is idempotent — re-running it
against an existing email updates that user instead.

## cert-manager: node-14's apiserver cannot reach the webhook

`condo-tls` was stuck unissued for 14h. Root cause was not condo's: flannel on
rke2-node-14 lost its Kubernetes API watch on 2026-08-06 and its VXLAN state
froze, so that node's *host* network cannot reach pods on other nodes. node-14
is still in the `kubernetes` Endpoints, so admission-webhook calls landing on
its apiserver time out. Proven per-apiserver:

```
kubectl --server=https://10.99.5.10:6443 apply --dry-run=server -f cert.yaml  -> OK
kubectl --server=https://10.99.5.11:6443 apply --dry-run=server -f cert.yaml  -> OK
kubectl --server=https://10.99.5.14:6443 apply --dry-run=server -f cert.yaml  -> failed calling webhook
```

Restarting the cert-manager controller re-rolls which apiserver its client-go
connection pins to (2 of 3 are healthy), which unblocked issuance. That is a
symptom fix — until flannel on node-14 is restarted, any controller can land on
its apiserver again and stall. See the node-14 memory note for the real fix.

## OIDC client for address-service

address-service builds an OIDC client against condo at boot and throws without
`OIDC_CONFIG`. The matching client must exist in condo's database:

```bash
kubectl -n condo exec deploy/condo -- sh -c 'cd /app/apps/condo && \
  node bin/create-oidc-client.js address-service <clientSecret> \
  http://address-service/oidc/callback'
```

## Not wired up

- **Email / SMS / push.** `NOTIFICATION__SEND_ALL_MESSAGES_TO_CONSOLE=true`, so
  notifications land in the pod log. Password resets and invites will not reach
  a real inbox until `EMAIL_API_CONFIG` is set.
- **Captcha.** `DISABLE_CAPTCHA=true` — no hCaptcha keys. Worth revisiting given
  the public host allows registration.
- **NATS messaging, AI flows, feature-flag service.** All left at their defaults.
- **Metrics.** Condo exposes `/server-health`, not a Prometheus `/metrics`
  endpoint, so there is no scrape job in `observability/02-config-prometheus.yaml`.
