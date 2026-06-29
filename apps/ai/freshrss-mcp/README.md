# freshrss-mcp

In-cluster [MCP](https://modelcontextprotocol.io) server that fronts the
`freshrss` namespace's FreshRSS instance, so Claude can read and manage feeds.

- **Image:** `ghcr.io/robertdwhite/freshrss-mcp`, built by CI in
  [github.com/RobertDWhite/freshrss-mcp](https://github.com/RobertDWhite/freshrss-mcp).
  Pin the digest in `kustomization.yaml`.
- **MCP endpoint:** `https://freshrss-mcp.internal.white.fm/mcp` (tailnet only),
  bearer-gated by `MCP_TOKEN`. `/healthz` is open.
- **Upstream:** talks to `http://freshrss.freshrss.svc.cluster.local` over the
  Google Reader API as the dedicated `claude` FreshRSS user.

## Secrets (`11-secret.sops.yaml`, SOPS/age)

| Key | Meaning |
| --- | --- |
| `MCP_TOKEN` | Bearer token clients send to `/mcp`. |
| `FRESHRSS_USER` | FreshRSS username (`claude`). |
| `FRESHRSS_API_PASSWORD` | That user's FreshRSS API password (Settings → Profile). |

`13-ghcr-pull.sops.yaml` is the shared ghcr.io image-pull dockerconfig.

## Notes

- The `claude` FreshRSS user must have an API password set and the GReader API
  enabled globally (Settings → Authentication → "Allow API access").
- FreshRSS stores per-user config under its data PVC at
  `data/users/<user>/`; that directory must be owned by uid 1000 (`abc`) or
  php-fpm reports "configuration cannot be found" and the API 401s.
