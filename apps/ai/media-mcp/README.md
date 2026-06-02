# media-mcp

A single remote MCP server that "joins" the whole media stack — the *arr suite,
Plex and Tautulli — behind one bearer-gated endpoint, so Claude can run
cross-tool workflows (look up a movie → add it to Radarr → force a
Prowlarr-backed search → trigger a Plex scan → confirm it landed → check who's
watching).

## What it is

- FastMCP server (`src/server.py`), streamable-HTTP transport, served at
  `https://media-mcp.internal.white.fm/mcp` (tailnet only).
- Inbound requests must present `Authorization: Bearer <MCP_TOKEN>`.
- Talks to each backend in-cluster (or, for Plex, over the LAN). A backend
  whose key is unset simply disables its own tools — it does not crash the
  server, and `media_overview` reports it as an error string.

## Backends & tools

| Backend  | Endpoint (env)                                   | Tools |
|----------|--------------------------------------------------|-------|
| Radarr   | `radarr.media…:7878` (`v3`)                       | `radarr_search`, `radarr_list`, `radarr_add`, `radarr_force_search`, `radarr_delete`, `radarr_queue`, `radarr_wanted` |
| Sonarr   | `sonarr.media…:8989` (`v3`)                       | `sonarr_search`, `sonarr_list`, `sonarr_add`, `sonarr_force_search`, `sonarr_delete`, `sonarr_queue`, `sonarr_wanted` |
| Prowlarr | `prowlarr.media…:9696` (`v1`)                     | `prowlarr_indexers`, `prowlarr_stats`, `prowlarr_search` |
| Readarr  | `readarr.media…:8787` (`v1`)                      | `readarr_authors`, `readarr_search`, `readarr_queue` |
| Bazarr   | `bazarr.media…:6767`                              | `bazarr_status`, `bazarr_wanted`, `bazarr_providers` |
| Plex     | `PLEX_URL` (`:32400`)                             | `plex_sessions`, `plex_libraries`, `plex_recently_added`, `plex_search`, `plex_scan_library` |
| Tautulli | `tautulli.tautulli…:8181`                         | `tautulli_activity`, `tautulli_history`, `tautulli_home_stats`, `tautulli_recently_added` |
| **join** | —                                                | `media_overview` — health + queue depth across every *arr, Plex now-playing, Tautulli stream count |

Notes:
- **Read + write** for Radarr/Sonarr (add, delete, force-search) and Plex
  (library scan). Prowlarr exposes manual indexer search.
- **Readarr** is read + queue only — its add flow is a brittle two-step
  author/book dance, intentionally omitted. Force a grab from Readarr's UI.
- **Bazarr** is read-only here (status / wanted / providers). It already
  auto-searches subtitles; its write API is version-fragile.
- **FileBot** is deliberately *not* included: it has no REST API (it's a
  licensed CLI that renames files on disk), so it doesn't fit this HTTP-wrapper
  pattern. The *arrs already auto-rename on import. Revisit only if you want a
  CLI-in-container with the media NFS mounted.

## One-time setup (you do this)

The *arr and Tautulli API keys are already seeded into `11-secret.sops.yaml`
(reused from `media/31-exportarr-secret.sops.yaml` and `tautulli/secret.sops.yaml`).
You only need to add Plex:

1. **Get your Plex token.** Sign in to Plex, open any library item → ⋯ → *Get
   Info* → *View XML*; the URL contains `X-Plex-Token=…`. Or pull it from the
   server's `Preferences.xml` (`PlexOnlineToken`).
2. **Fill the secret:**
   ```sh
   sops media-mcp/11-secret.sops.yaml
   # PLEX_URL   -> http://<plex-lan-ip>:32400
   # PLEX_TOKEN -> the token from step 1
   ```
3. **Add the DNS record:** add `media-mcp.internal.white.fm` to
   `technitium/35-zones-secret.sops.yaml` pointing at the gateway IP (same as
   `monica-mcp.internal.white.fm`).
4. Commit + push; ArgoCD syncs the `media-mcp` Application.

## Registering it with Claude

Get the inbound token:

```sh
sops -d media-mcp/11-secret.sops.yaml | grep MCP_TOKEN
```

```sh
claude mcp add --transport http media \
  https://media-mcp.internal.white.fm/mcp \
  --header "Authorization: Bearer <MCP_TOKEN>"
```

You must be on the Headscale tailnet for the hostname to resolve. Once
connected, `media_overview` confirms every backend is reachable.

## Updating the image

```sh
docker buildx build --platform linux/amd64 --load \
  -t registry.internal.white.fm/media-mcp:<ver> src/
docker push registry.internal.white.fm/media-mcp:<ver>
# bump newTag in kustomization.yaml, commit, push (ArgoCD syncs)
```
