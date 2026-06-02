# monica-mcp

A remote MCP server that exposes [Monica](../../monica)'s contacts, important dates
and tasks to Claude. Monica v5 dropped its REST API, so this talks to the only
structured interface v5 publishes: the **CardDAV/CalDAV** endpoint at `/dav`.

## What it is

- FastMCP server (`src/server.py`), streamable-HTTP transport, served at
  `https://monica-mcp.internal.white.fm/mcp` (tailnet only).
- Talks to Monica in-cluster at `http://monica.monica.svc.cluster.local:80/dav`
  using HTTP Basic `MONICA_EMAIL:MONICA_DAV_TOKEN`.
- Inbound requests must present `Authorization: Bearer <MCP_TOKEN>`.
- Secrets live in `11-secret.sops.yaml` (`MONICA_EMAIL`, `MONICA_DAV_TOKEN`,
  `MCP_TOKEN`).

## Tools

`list_contacts`, `search_contacts`, `get_contact`, `upcoming_dates`,
`list_tasks`, `dav_collections` (diagnostic).

**What's available:** vCard 4.0 fields — names, emails, phones, addresses,
org/title, birthdays, social profiles; CalDAV "dates" (VEVENT incl. recurring
birthdays) and "tasks" (VTODO). **Not available over DAV:** notes, photos,
relationships, journal entries — Monica simply doesn't export them.

## One-time setup (you do this)

1. **Generate a Monica API token.** In Monica → Settings → API, create a
   personal access token with **both `read` and `write` abilities** (read-only
   tokens 403 even for reads). Copy it.
2. **Fill the secret:**
   ```sh
   sops monica-mcp/11-secret.sops.yaml
   # set MONICA_EMAIL to your Monica login email (must match exactly)
   # set MONICA_DAV_TOKEN to the token from step 1
   ```
3. **Add the DNS record:** add `monica-mcp.internal.white.fm` to
   `technitium/35-zones-secret.sops.yaml` pointing at the gateway IP (same as
   `jetlog-mcp.internal.white.fm`).
4. Commit + push; ArgoCD syncs the `monica-mcp` Application.

## Registering it with Claude

Get the inbound token:

```sh
sops -d monica-mcp/11-secret.sops.yaml | grep MCP_TOKEN
```

Add the remote MCP (Claude Code):

```sh
claude mcp add --transport http monica \
  https://monica-mcp.internal.white.fm/mcp \
  --header "Authorization: Bearer <MCP_TOKEN>"
```

You must be on the Headscale tailnet for the hostname to resolve. Once
connected, `dav_collections` confirms auth + discovery are working.

## Updating the image

```sh
docker buildx build --platform linux/amd64 --load \
  -t registry.internal.white.fm/monica-mcp:<ver> src/
docker push registry.internal.white.fm/monica-mcp:<ver>
# bump newTag + digest in kustomization.yaml, commit, push (ArgoCD syncs)
```
