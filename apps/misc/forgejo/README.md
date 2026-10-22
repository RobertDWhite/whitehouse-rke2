# Forgejo

Internal-only Git forge at https://git.internal.white.fm (rootless image,
SQLite, config at `/var/lib/gitea/custom/conf/app.ini` on the data PVC,
generated from the `FORGEJO__section__KEY` env vars in `30-deployment.yaml`).

## Sign-in

- **Authentik (OIDC)** - "Sign in with authentik" on `/user/login`.
  - Authentik: OAuth2/OpenID provider `Provider for Forgejo` + application
    `forgejo` (slug), bound to the `authentik Admins` group, so only members
    of that group can log in.
  - Forgejo: OAuth2 auth source named `authentik` (provider `openidConnect`,
    discovery URL
    `https://auth.white.fm/application/o/forgejo/.well-known/openid-configuration`).
    The `oauth-source` init container creates it on first start and runs
    `forgejo admin auth update-oauth` on every later start, so the DB always
    matches the `forgejo-oidc` Secret (`20-secret-oidc.sops.yaml`, keys
    `client-id` / `client-secret`).
  - `[oauth2_client] ACCOUNT_LINKING = auto` with `USERNAME = nickname`: an
    Authentik login whose username (or, failing that, email) matches an
    existing Forgejo user is linked to that user instead of creating a new one.
    New Authentik users get an account auto-registered.
- **Local password** - kept as break-glass for the local admin. The login form
  stays enabled; only self sign-up is off (`ALLOW_ONLY_EXTERNAL_REGISTRATION`).
  Reset if needed:
  `kubectl -n forgejo exec deploy/forgejo -- forgejo admin user change-password -u <user> -p '<new>'`

## Recreate the Authentik side

Same approach as Technitium (`platform/networking/technitium/bin/setup-authentik-sso.md`):
use the Authentik API with the bootstrap token from
`security/authentik/authentik-env.sops.yaml`.

1. `POST /api/v3/providers/oauth2/` with
   - `name: Provider for Forgejo`, `client_type: confidential`
   - `grant_types: [authorization_code, refresh_token]` (required - an empty
     list makes `/authorize` fail with "request is otherwise malformed")
   - `redirect_uris: [{matching_mode: strict, url: https://git.internal.white.fm/user/oauth2/authentik/callback}]`
   - `authorization_flow` (default-provider-authorization-explicit-consent),
     `invalidation_flow`, `signing_key` and `property_mappings`
     (openid/email/profile) copied from an existing OIDC provider such as
     `Provider for Technitium SSO`
   - `sub_mode: hashed_user_id`, `issuer_mode: per_provider`,
     `include_claims_in_id_token: true`
2. `POST /api/v3/core/applications/` with `slug: forgejo`, `name: Forgejo`,
   `provider: <pk>`, `meta_launch_url: https://git.internal.white.fm/user/oauth2/authentik`.
3. `POST /api/v3/policies/bindings/` binding group `authentik Admins` to the
   application (`target: <application pk>`, `order: 0`).
4. Put the generated `client_id` / `client_secret` into
   `20-secret-oidc.sops.yaml` and re-encrypt:
   `sops --encrypt --encrypted-regex '^(data|stringData|parameters)$' --in-place 20-secret-oidc.sops.yaml`.
   The next pod start applies them via the init container.
