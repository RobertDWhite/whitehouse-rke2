# MCP services

These 14 services are deployed by this repository. Prefer their MCP tools for the corresponding task instead of rebuilding API requests or using a browser. Read the server's instructions and tool schema before use; the live tool list is authoritative.

All endpoints below use streamable HTTP and bearer authentication. The internal addresses require LAN or tailnet access. Credentials belong in private local client configuration, never in this document or other plaintext files committed to Git.

| Client name | Use for | Endpoint | Credential source in this repository |
| --- | --- | --- | --- |
| `brain` | Search and read the second-brain repository, entities, recent changes, directory outlines, and sync status; read-only | `https://brain-mcp.internal.white.fm/mcp` | `apps/ai/brain/13-mcp-secret.sops.yaml`: `MCP_TOKEN` |
| `graphiti` | Search temporal knowledge-graph facts and entities; add and manage graph memories | `https://graphiti.internal.white.fm/mcp` | `apps/ai/graph/11-secret.sops.yaml`, Secret `graphiti-mcp-api-keys`: client key |
| `obsidian` | Search, read, write, edit, move, and delete LiveSync vault notes | `https://obsidian-mcp.internal.white.fm/mcp` | `apps/data/obsidian/13-secret-obsidian-mcp.sops.yaml`: `MCP_AUTH_TOKEN` |
| `meerkat` | Contacts, circles, birthdays, notes, activities, reminders, and relationships | `https://meerkat-mcp.internal.white.fm/mcp` | `apps/ai/meerkat-mcp/11-secret.sops.yaml`: `MCP_TOKEN` |
| `nodebyte` | Inventory devices, sites, and services; search and manage nodes and list teams | `https://nodebyte-mcp.internal.white.fm/mcp` | `apps/ai/nodebyte-mcp/11-secret.sops.yaml`: `MCP_TOKEN` |
| `pages` | List, inspect, deploy, and delete self-hosted static sites | `https://pages-mcp.internal.white.fm/mcp` | `apps/ai/pages/11-secret.sops.yaml`: `MCP_TOKEN` |
| `freshrss` | Feed subscriptions, categories, articles, unread status, and stars | `https://freshrss-mcp.internal.white.fm/mcp` | `apps/ai/freshrss-mcp/11-secret.sops.yaml`: `MCP_TOKEN` |
| `googlenews` | Top, topic, and local headlines; news search and redirect decoding | `https://googlenews-mcp.internal.white.fm/mcp` | `apps/ai/googlenews-mcp/11-secret.sops.yaml`: `MCP_TOKEN` |
| `gsc` | Google Search Console sites, verification, ownership, sitemaps, search analytics, URL inspection, and IndexNow | `https://gsc-mcp.internal.white.fm/mcp` | `apps/ai/gsc-mcp/11-secret.sops.yaml`: `MCP_TOKEN` |
| `congress` | Disclosed congressional trades, provenance, members, signals, policy context, research, and strategy analytics; read-only | `https://congress-mcp.internal.white.fm/mcp` | `apps/ai/congress-mcp/11-secret.sops.yaml`: `MCP_TOKEN` |
| `jetlog` | Flight log, companions, frequent-flyer records, boarding passes, airport/airline lookup, and travel analytics | `https://jetlog-mcp.internal.white.fm/mcp` | `apps/ai/jetlog-mcp/11-secret.sops.yaml`: `MCP_TOKEN` |
| `media` | Plex/Tautulli activity and libraries; Radarr, Sonarr, Prowlarr, Readarr, Bazarr, and LazyLibrarian search and management | `https://media-mcp.internal.white.fm/mcp` | `apps/ai/media-mcp/11-secret.sops.yaml`: `MCP_TOKEN` |
| `clean-audio` | Inspect media dialogue and manage clean-audio/subtitle jobs, reports, word lists, and sidecars | `https://clean-audio-mcp.internal.white.fm/mcp` | `apps/ai/clean-audio-mcp/11-secret.sops.yaml`: `MCP_TOKEN` |
| `sdr` | Radio recordings, transcripts, repeaters, APRS packets/stations, activity, hardware health, storage, and alerts; read-only | `https://sdr-mcp.internal.white.fm/mcp` | `apps/radio/sdr-research/22-secret.sops.yaml`: `mcp-token` |

## Choosing a source

- Use `brain` for source notes and citations, `graphiti` for relationships and temporal facts, and `obsidian` for direct LiveSync note editing. These are distinct stores/interfaces.
- Graphiti defaults to the `brain` group. Inspect tool schemas before naming another group.
- For Pages sites managed by brain-sync, edit the owning brain repository's `Pages/<site>/` source. Direct deployments are overwritten by sync. See [Pages deployment notes](apps/ai/pages/README.md).
- Meerkat is the CRM used by the current brain pipeline. Monica and its MCP server were removed on 2026-10-26.
- Use the user's requested scope for writes. Adding a server does not itself authorize editing records, sending communications, deleting data, or changing infrastructure.

## Local app setup

The ChatGPT desktop app, Codex CLI, and IDE extension on the same host share `~/.codex/config.toml` ([official MCP setup documentation](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)). Each client name above corresponds to an `mcp_servers` entry. Existing stdio bridges can also connect to these HTTP endpoints.

On Robert's Mac, all 14 services were configured and authenticated initialization plus complete `tools/list` discovery succeeded on 2026-10-23: 205 tools total. This verifies connectivity and discovery, not every upstream operation. Refresh/restart MCP connections in app settings when an existing chat still has an older tool catalog.

The local configuration contains credentials and must remain outside Git with owner-only file permissions. For another machine, obtain its credentials from SOPS through the normal authorized setup; do not copy tokens into this guide. An endpoint failure should be diagnosed before substituting another service or modifying the deployment.
