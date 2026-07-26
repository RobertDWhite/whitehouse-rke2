# gsc-mcp

In-cluster [MCP](https://modelcontextprotocol.io) server for Google Search
Console and IndexNow — search-engine submission and analytics via APIs
instead of browser automation.

- **Image:** `ghcr.io/robertdwhite/gsc-mcp`, built by CI in
  [github.com/RobertDWhite/gsc-mcp](https://github.com/RobertDWhite/gsc-mcp).
  Pin the tag/digest in `kustomization.yaml`.
- **MCP endpoint:** `https://gsc-mcp.internal.white.fm/mcp` (tailnet only),
  bearer-gated by `MCP_TOKEN`. `/healthz` is open.
- **Tools:** gsc_status, list_sites, add_site, verification_token,
  verify_site, add_owner, submit_sitemap, list_sitemaps, search_analytics,
  inspect_url, submit_indexnow.

## Secrets (`11-secret.sops.yaml`, SOPS/age)

| Key | Meaning |
| --- | --- |
| `MCP_TOKEN` | Bearer token clients send to `/mcp`. |
| `GSC_SA_JSON` | Google service-account key JSON. **Empty until the one-time GCP setup** (create SA, enable "Google Search Console API" + "Site Verification API", create JSON key). Google tools error clearly until set; IndexNow works regardless. |
| `INDEXNOW_KEY` | IndexNow key for solacoffea.app (key file already deployed on the site). |

`13-ghcr-pull.sops.yaml` is the shared ghcr.io image-pull dockerconfig.

## First-run flow once GSC_SA_JSON is set

`add_site("https://solacoffea.app/")` → `verification_token(...)` (place the
token file on the site) → `verify_site(...)` → the service account is a
verified owner → `submit_sitemap`, `search_analytics`, `inspect_url` all work.
`add_owner(site, "robert@...")` grants the human account UI access.
