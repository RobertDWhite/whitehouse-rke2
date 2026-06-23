# pages

Self-hosted static-site host with an MCP upload tool. Source:
[`github.com/RobertDWhite/pages-mcp`](https://github.com/RobertDWhite/pages-mcp)
(also cloned at `~/Documents/GitHub/pages-mcp`).

- **Serve:** `https://<site>.pages.internal.white.fm/` — one subdomain per site,
  open on the internal (tailnet-only) network, no Authentik gate. The apex
  `https://pages.internal.white.fm/` shows a directory index of all sites.
- **Upload (MCP):** `https://pages-mcp.internal.white.fm/mcp` —
  `Authorization: Bearer <MCP_TOKEN>` (`11-secret.sops.yaml`).
- **Storage:** Longhorn PVC `pages-data` (5Gi) at `/data/sites`, one dir per site.

One Deployment serves both planes on `:8080`, split by Host header. The apex
`pages.internal.white.fm` + the MCP host ride the shared `*.internal.white.fm`
wildcard listener/cert, but the per-site subdomains are a **nested** wildcard
(`*.pages.internal.white.fm`) — two labels deep — so they need a dedicated gateway
listener (`https-pages-internal`) plus a cert SAN, both in
`platform/networking/envoy-gateway/` (`20-gateway.yaml`, `05-certificates.yaml`).
external-dns auto-creates the DNS record from the wildcard HTTPRoute's hostname.

## MCP tools

`deploy_site(name, files, replace=True)`, `list_sites()`, `get_site(name)`,
`delete_site(name)`. See the source README for the `files` shape.

## Build / deploy

CI builds `ghcr.io/robertdwhite/pages-mcp` (amd64+arm64) on push to `main`/`v*`.
Pin the digest in `kustomization.yaml`, commit, push — ArgoCD syncs.

```sh
crane digest ghcr.io/robertdwhite/pages-mcp:latest   # -> paste into kustomization.yaml
```

## Register with Claude Code

```sh
sops -d apps/ai/pages/11-secret.sops.yaml | grep MCP_TOKEN
claude mcp add --transport http pages \
  https://pages-mcp.internal.white.fm/mcp \
  --header "Authorization: Bearer <MCP_TOKEN>"
```
