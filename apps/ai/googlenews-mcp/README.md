# googlenews-mcp

In-cluster [MCP](https://modelcontextprotocol.io) server in front of Google
News, so internet-isolated agents (e.g. Hermes) can read the news: the agent
talks only to this server, and this server talks only to `news.google.com`
(public RSS + the redirect-decoding RPC). Deliberately not a general web
proxy — there is no fetch-arbitrary-URL tool.

- **Image:** `ghcr.io/robertdwhite/googlenews-mcp`, built by CI in
  [github.com/RobertDWhite/googlenews-mcp](https://github.com/RobertDWhite/googlenews-mcp).
  Pin the tag/digest in `kustomization.yaml`.
- **MCP endpoint:** `https://googlenews-mcp.internal.white.fm/mcp` (tailnet
  only), bearer-gated by `MCP_TOKEN`. `/healthz` is open.
- **Upstream:** `news.google.com` only; no API key needed.
- **Tools:** `top_headlines`, `topic_headlines`, `search_news`,
  `geo_headlines`, `decode_urls`.

## Secrets (`11-secret.sops.yaml`, SOPS/age)

| Key | Meaning |
| --- | --- |
| `MCP_TOKEN` | Bearer token clients send to `/mcp`. |

`13-ghcr-pull.sops.yaml` is the shared ghcr.io image-pull dockerconfig.

## Notes

- Needs egress to the internet (news.google.com); ingress is locked to the
  envoy gateway like the other MCPs.
- `decode_urls` (redirect link -> publisher URL) is best-effort; Google
  changes that private endpoint occasionally.
