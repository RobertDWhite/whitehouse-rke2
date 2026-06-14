# listmonk

Self-hosted newsletter & mailing-list manager (campaign manager for the Sola Coffea
coffee-shop outreach). Single Go binary + its own Postgres.

- **URL:** https://listmonk.white.fm (public, no SSO — see routing note below)
- **Image:** `listmonk/listmonk:v6.1.0`, Postgres `postgres:17-alpine`
- **Namespace:** `listmonk`
- **Admin login:** the super-admin user/password are injected from the `listmonk-secrets`
  Secret (`admin-user` / `admin-password`) via `LISTMONK_ADMIN_USER` /
  `LISTMONK_ADMIN_PASSWORD` on first install. Read them with
  `sops -d 05-secret.sops.yaml`.

## Routing — why this host bypasses Authentik

Every other `*.white.fm` app routes through the Authentik embedded outpost. listmonk
does **not**: it serves recipient-facing endpoints that must stay public —
one-click unsubscribe, open/click tracking pixels, and public subscription/archive
pages. Gating the host behind SSO would break CAN-SPAM unsubscribe links and tracking
for recipients (coffee-shop owners, who are not Authentik users). The admin UI at
`/admin` is protected by listmonk's own built-in super-admin login instead.

## One-time setup in the admin UI (NOT manifest-managed)

listmonk stores SMTP and the public root URL in its **database** (Settings UI), not in
config/env. After first deploy, log into https://listmonk.white.fm/admin and set:

**Settings → General**
- Root URL: `https://listmonk.white.fm`
- "From" email: `Sola Coffea <robert@solacoffea.com>`

**Settings → SMTP** (values mirror 1Password item `@solacoffea.com smtp`,
uuid `viargrl4bsy46pz42mwscsn63i`, and are stored in `listmonk-secrets` for reference):
- Host: `smtp.protonmail.ch`
- Port: `587`
- Auth protocol: `LOGIN`
- Username: `robert@solacoffea.com`
- Password: the Proton SMTP token (`sops -d 05-secret.sops.yaml` → `smtp-password`)
- TLS: `STARTTLS`
- Max connections: keep low (1–2). Proton Business enforces a daily send cap
  (a few hundred/day) — set a per-campaign rate limit so sends drip, not blast.

## Secrets

`05-secret.sops.yaml` (SOPS/age, decrypted in-cluster by KSOPS) holds:
`db-password`, `admin-user`, `admin-password`, and the Proton SMTP
`smtp-host`/`smtp-port`/`smtp-username`/`smtp-password` (reference copies for the UI step).

## Storage

- Postgres data: StatefulSet `volumeClaimTemplate` (`longhorn`, 10Gi) — source of truth.
- Uploads (campaign media): `listmonk-uploads` PVC in
  `platform/storage/storage/listmonk/` (`longhorn`, 5Gi), mounted at `/listmonk/uploads`.

Namespace `listmonk` is in the Velero `daily-critical` schedule.
