# Hermes Media for Matrix

`@hermes-media:white.fm` is separate from the existing Hermes account. It serves one invite-only, non-federated, encrypted room for `@robert:white.fm`. The gateway accepts decrypted text only from that room and sender, rejects new invites, and does not process attachments. Only in-cluster Synapse, Qwen, DNS and the bearer-gated media context service are allowed by the gateway's network policy. The room's device and local session state survive restarts on `hermes-media-state`.

## What it can do

- Check Sonarr, Radarr, Prowlarr, Readarr, Bazarr, LazyLibrarian, Plex and Tautulli through their existing media MCP integrations. Start an explicit add, force-search or named Plex scan and verify the resulting application state.
- Inspect media pod readiness, restart counts and jobs through namespace-scoped read permissions. Compare live FileBot AMC settings and the Synology handoff with actual destination-file metadata and Plex results.
- Query bounded, live metadata for the configured Synology libraries, import directories and downloader landing folders, including MeTube and qBittorrent. Files are not copied, indexed, opened or written by the context broker. This does **not** expose live NAS downloader task APIs, raw NAS content, Immich catalogs or private home videos.
- The broker refuses deletion, manual moves, legacy NAS FileBot SSH tools, Kubernetes writes, and arbitrary paths or URLs. Existing media apps still use their configured indexers/providers and VPN paths. Local inference does not make those existing integrations offline.

Try asking: “Is anything stuck between Radarr or Sonarr and Plex?”, “What is the live cluster FileBot input and conflict policy?”, or “Check the latest MeTube arrivals without changing anything.”

## Provision and verify

On an operator host that is authenticated to this cluster, run `python3 apps/misc/hermes/media/provision_matrix.py provision` and save its **encrypted** output as `apps/misc/hermes/media/matrix.sops.yaml`. It creates the dedicated non-admin account and a private room if the Kubernetes Secret does not exist. It does not reset an existing account or room. Do not publish or copy plaintext credentials. Run the same script with `verify` to check the existing room and with `invite` to invite the configured operator after the gateway is ready.

The `media-mcp` Argo app owns the context broker, and the `hermes` Argo app owns the Matrix gateway. The nested Kustomizations can also be rendered without SOPS for per-component validation. Apply the broker before the gateway; its catalog has local diagnostics even if upstream media MCP is temporarily unavailable. Check the gateway's Matrix connection, actual tool discovery, a live NAS metadata call and a model/tool turn before declaring the integration operational. A test from the operator's Matrix client is needed to verify encrypted replies end to end.

The two `context-auth.sops.yaml` manifests hold the same dedicated client token in their respective namespaces. Rotate both together with a new random value, then restart both deployments to reload their Secret-backed environment. This credential is not the upstream media MCP token.
