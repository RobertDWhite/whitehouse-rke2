## Backoffice Dashboard for whitehouse-rke2 - Handoff Context

### What was built
A cluster backoffice dashboard app (FastAPI + Next.js + PostgreSQL) in the `backoffice/` directory of https://github.com/RobertDWhite/whitehouse-rke2 on branch `claude/fix-502-error-8SpIg`.

### Architecture
- **Backend**: FastAPI at `backoffice/backend/` - uses the Kubernetes Python client with an in-cluster ServiceAccount to query live cluster state (deployments, pods, services, ingresses, nodes). PostgreSQL for storing app notes/metadata.
- **Frontend**: Next.js 15 + Tailwind CSS (dark theme) at `backoffice/frontend/` - server-side rendered pages for Overview, Applications, Ingresses, Services, Nodes, and Authentik-protected apps.
- **K8s manifests**: Follow the repo's numbered-file convention (`00-namespace.yaml` through `51-ingress-internal.yaml`), with ArgoCD Application at `argo-cd/applications/backoffice-app.yaml`.
- **Auth**: Authentik proxy on `backoffice.white.fm` (public), direct access on `backoffice.internal.white.fm` (internal). Uses the same ExternalName service pattern as all other apps in the repo.
- **Images**: `registry.white.fm/backoffice-api:0.1.0` and `registry.white.fm/backoffice-web:0.1.0`

### What's left to do
1. **The SOPS secret file needs to be created and encrypted.** The file `backoffice/11-postgres-secret.sops.yaml` was removed from the repo because it couldn't be encrypted in the cloud environment (no age private key available). Recreate it:
```yaml
apiVersion: v1
kind: Secret
metadata:
  name: backoffice-postgres-credentials
  namespace: backoffice
type: Opaque
stringData:
  POSTGRES_DB: backoffice
  POSTGRES_USER: backoffice
  POSTGRES_PASSWORD: <set a real password>
```
Then encrypt: `sops -e -i backoffice/11-postgres-secret.sops.yaml`

2. **The ksops.yaml generator and kustomization.yaml already reference this file** - they're wired up and waiting for it.

3. **Docker images need to be built and pushed:**
```bash
docker build -t registry.white.fm/backoffice-api:0.1.0 backoffice/backend/
docker build -t registry.white.fm/backoffice-web:0.1.0 backoffice/frontend/
docker push registry.white.fm/backoffice-api:0.1.0
docker push registry.white.fm/backoffice-web:0.1.0
```

4. **Create an Authentik provider/application** for `backoffice.white.fm` in the Authentik admin panel.

### Key repo patterns followed
- SOPS + age encryption via ksops generators (public key: `age16krjysalsq26mfndnthd9r42thapj43a0zdndgrrz30utzuhwd0q7fxh9p`)
- Longhorn storage class for PVCs
- Read-only ClusterRole (`backoffice-reader`) for the API ServiceAccount
- ArgoCD auto-sync with prune + selfHeal
- Authentik proxy via ExternalName service pointing to `authentik-server.authentik.svc.cluster.local:9443`

### Branch
`claude/fix-502-error-8SpIg` - also contains a ground-station health probe fix (first commit).
