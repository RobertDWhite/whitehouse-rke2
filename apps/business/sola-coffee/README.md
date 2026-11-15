# Sola Coffee hosted app

Public URL: https://app.blackswancoffeecart.com

The Cloudflare zone belongs to account `4d67fa0412f2cd566500d1f797458f14` and uses the locally managed tunnel `221e59d6-9538-4e44-bdaa-a89b5020f7cf`. Its route goes directly to `http://sola.sola-coffee.svc.cluster.local:80`; the app handles Google sign-in. Do not put an infrastructure SSO proxy in front of customer forms or OAuth callbacks.

- Source: https://github.com/RobertDWhite/sola-coffee-quoter
- Google Cloud project: `black-swan-coffee` (`544359374994`).
- Initial confirmed Google owners: `robert@soluscoffee.com`, `ben@soluscoffee.com`.
- Hosted PostgreSQL 17 uses a retained, replicated Longhorn volume. Local SQLite was imported before the app first started; original snapshot remains in the source checkout's ignored `data/backups`.
- Google client credentials and the integration encryption key are SOPS encrypted in `10-secret.sops.yaml`.
- Customer intake remains disabled pending approved pricing and SMTP setup. Google Calendar still needs owner consent and calendar selection.
- One app replica initially. Database job claims and tenant writes are transactional; rate limits are process-local until distributed abuse limiting is added.

## Backups

`sola-postgres-backup` makes a custom-format dump nightly at 06:25 UTC to a separate retained 20 GiB Longhorn PVC and keeps 14 days. It validates the archive index; a separate restore test should be repeated after schema changes. The existing Velero `daily-critical` schedule includes this namespace and its volumes. Check actual scheduled backup results rather than assuming a configured schedule proves recoverability. The previously attempted NAS export denied service-user writes and is not used by this app.

Keep the SOPS age key and integration encryption key recoverable independently. A database restore without its integration key cannot decrypt calendar credentials.

## Release and rollback

The image is pinned by digest in `kustomization.yaml`. Build a new immutable image from the app checkout, validate tests, and update the digest here. Argo owns persistent resources.

For a bad release, revert the app image digest through Git. For a bad first launch, remove the app hostname's DNS record and tunnel route, and scale the app to zero while preserving database and backup PVCs. Do not delete or prune PostgreSQL data to roll back code. Database schema changes require a compatible restore/migration plan.
