# pages

Self-hosted static-site host with an MCP upload tool. Source:
[`github.com/RobertDWhite/pages-mcp`](https://github.com/RobertDWhite/pages-mcp)
(also mirrored at `~/Documents/GitHubCurrent/pages-mcp`).

- **Serve:** `https://pages.internal.white.fm/<site>/` — open on the internal
  (tailnet-only) network, no Authentik gate. A directory index lists all sites.
- **Upload (MCP):** `https://pages-mcp.internal.white.fm/mcp` —
  `Authorization: Bearer <MCP_TOKEN>` (`11-secret.sops.yaml`).
- **Storage:** Longhorn PVC `pages-data` (5Gi) at `/data/sites`, one dir per site.

One Deployment serves both planes on `:8080`, split by Host header. The shared
`*.internal.white.fm` wildcard listener + cert cover both hostnames — no dedicated
gateway listener, cert, or DNS record needed.

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
