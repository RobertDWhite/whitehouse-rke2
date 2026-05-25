# jetlog-mcp

A remote MCP server that wraps the [jetlog](../jetlog) flight-log API so Claude can
read, add, and analyze flights — including logging flights straight from booking
confirmation emails or boarding passes.

## What it is

- FastMCP server (`src/server.py`), streamable-HTTP transport, served at
  `https://jetlog-mcp.internal.white.fm/mcp` (tailnet only).
- Talks to jetlog in-cluster at `http://jetlog.jetlog.svc.cluster.local:3000`
  using a long-lived jetlog **API key** (`Authorization: Bearer`).
- Inbound requests must present `Authorization: Bearer <MCP_TOKEN>`.
- Both secrets live in `11-secret.sops.yaml` (`JETLOG_API_KEY`, `MCP_TOKEN`).

## Tools

`list_flights`, `get_flight`, `add_flight`, `update_flight`, `delete_flight`,
`check_duplicate`, `parse_boarding_pass`, `enrich_flights`, `search_airports`,
`search_airlines`, `get_statistics`, `get_analytics`.

## Registering it with Claude

Get the inbound token:

```sh
sops -d jetlog-mcp/11-secret.sops.yaml | grep MCP_TOKEN
```

Add the remote MCP (Claude Code example):

```sh
claude mcp add --transport http jetlog \
  https://jetlog-mcp.internal.white.fm/mcp \
  --header "Authorization: Bearer <MCP_TOKEN>"
```

You must be on the Headscale tailnet for the hostname to resolve.

## Logging flights from email

With both the Gmail MCP and this server connected, ask Claude something like:

> "Check my inbox for flight confirmations from the last month and log any that
> aren't already in jetlog."

Claude reads the confirmation, resolves airports with `search_airports`, calls
`check_duplicate`, then `add_flight`. For a boarding pass, feed the scanned BCBP
barcode string to `parse_boarding_pass` first.

## Updating the image

```sh
docker buildx build --platform linux/amd64 --load \
  -t registry.internal.white.fm/jetlog-mcp:<ver> src/
docker push registry.internal.white.fm/jetlog-mcp:<ver>
# bump newTag + digest in kustomization.yaml, commit, push (ArgoCD syncs)
```

## Rotating credentials

- jetlog API key: manage under jetlog → Settings → API keys (key `claude-mcp`,
  id 1). Update `JETLOG_API_KEY` in the secret if rotated.
- `MCP_TOKEN`: regenerate with `python3 -c 'import secrets; print(secrets.token_urlsafe(32))'`,
  update the secret and re-register the MCP client.
