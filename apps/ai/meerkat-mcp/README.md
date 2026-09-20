# meerkat-mcp

A remote MCP server that exposes [Meerkat CRM](../../misc/meerkat)'s contacts,
notes, activities, reminders and relationships to Claude via Meerkat's REST API.

## What it is

- FastMCP server (source: `RobertDWhite/meerkat-mcp`), streamable-HTTP
  transport, served at `https://meerkat-mcp.internal.white.fm/mcp` (tailnet only).
- Talks to Meerkat in-cluster at `http://meerkat.meerkat.svc.cluster.local:80/api/v1`
  using `Authorization: Bearer <MEERKAT_API_TOKEN>`.
- Inbound requests must present `Authorization: Bearer <MCP_TOKEN>`.
- Secrets live in `11-secret.sops.yaml` (`MEERKAT_API_TOKEN`, `MCP_TOKEN`).

## Tools

Contacts: `list_contacts`, `get_contact`, `create_contact`, `update_contact`,
`archive_contact`, `delete_contact`, `list_circles`, `upcoming_birthdays`.
Notes: `list_notes`, `add_note`, `update_note`, `delete_note`.
Activities: `list_activities`, `add_activity`, `update_activity`, `delete_activity`.
Reminders: `list_reminders`, `add_reminder`, `complete_reminder`, `delete_reminder`.
Relationships: `list_relationships`, `add_relationship`, `delete_relationship`.
Diagnostic: `whoami`.

## One-time setup (you do this)

1. **Create a Meerkat API token.** In Meerkat → Settings → API Tokens, create a
   token; copy the `meerkat_…` value (shown once).
2. **Fill the secret:**
   ```sh
   sops apps/ai/meerkat-mcp/11-secret.sops.yaml
   # set MEERKAT_API_TOKEN to the token from step 1 (MCP_TOKEN is pre-generated)
   ```
3. **Publish the image:** push `RobertDWhite/meerkat-mcp` to GitHub and tag
   `v0.1.0`; the workflow builds `ghcr.io/robertdwhite/meerkat-mcp:v0.1.0`.
4. Commit + push; ArgoCD syncs the `meerkat-mcp` Application.

## Registering it with Claude

```sh
sops -d apps/ai/meerkat-mcp/11-secret.sops.yaml | grep MCP_TOKEN
claude mcp add --transport http meerkat \
  https://meerkat-mcp.internal.white.fm/mcp \
  --header "Authorization: Bearer <MCP_TOKEN>"
```

You must be on the Headscale tailnet for the hostname to resolve. Once
connected, `whoami` confirms both tokens are working.
