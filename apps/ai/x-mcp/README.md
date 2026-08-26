# x-mcp

MCP server in front of the X (Twitter) API v2, for agents that need a voice on X.

- Source: https://github.com/RobertDWhite/x-mcp
- Image: `ghcr.io/robertdwhite/x-mcp` (pinned to a tag in `kustomization.yaml`)
- Endpoint: `https://x-mcp.internal.white.fm/mcp` (internal only, bearer-gated)

## Posting is deliberately two-step

Reads are unrestricted. Writes are not: `draft_post` validates and returns a
preview plus a **single-use** `confirm_token`, and nothing is published until
`publish_post` is called with that token. An agent therefore cannot post in one
call — a human-visible preview always exists first. Tokens expire
(`X_DRAFT_TTL`, default 600s) and drafts live only in memory, so a stale token
never outlives the process that showed the preview.

There is **no delete tool**. Deletion is irreversible; remove posts from the X app.

## Credentials

`x-mcp-env` (SOPS, `11-secret.sops.yaml`) holds `MCP_TOKEN` plus the four X
OAuth 1.0a values. **The four X values ship empty** — the server returns a clear
"not configured" error until they are set, the same way `gsc-mcp` waited on its
service account.

To fill them in:

```sh
sops apps/ai/x-mcp/11-secret.sops.yaml   # set X_API_KEY / X_API_SECRET /
                                         # X_ACCESS_TOKEN / X_ACCESS_TOKEN_SECRET
kubectl -n x-mcp rollout restart deploy/x-mcp
```

Get them from an X app with **Read and Write** permission: the consumer key and
secret, plus an access token/secret pair generated for the account that should
be posting. App-only bearer tokens cannot post — publishing acts *as* the
account, which requires user context.

Note that X gates write access behind its paid API tiers; a `429` surfaces the
rate-limit reset when X provides one.

## Wiring

DNS is not automatic — `x-mcp.internal.white.fm` is an A record in
`platform/networking/technitium/35-zones-secret.sops.yaml`. See the
house-mcp-recipe notes. To expose it to Hermes, add it to `mcp_servers:` in
`/opt/data/config.yaml` on the hermes-data PVC with the token from
`~/kube/secrets/x-mcp-token.txt`.
