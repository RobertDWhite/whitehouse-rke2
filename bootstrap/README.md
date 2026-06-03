# bootstrap/

The **only** manifests applied by hand. Everything else in this repo is
reconciled by ArgoCD from git.

On a fresh cluster, after ArgoCD itself is installed, seed the app-of-apps
pattern by applying these three root Applications:

```sh
kubectl apply -f bootstrap/app-of-apps.yaml   # → syncs argo-cd/applications/ (99 apps + 4 AppProjects)
kubectl apply -f bootstrap/app-of-repos.yaml  # → syncs argo-cd/helm-repos/
kubectl apply -f bootstrap/app-of-crds.yaml   # → syncs argo-cd/crds/
```

From there, ArgoCD reconciles the whole tree. Nothing else is applied manually.

## Notes

- These roots are **not** managed by any Application (no `app-of-apps`
  tracking id) — they are the seed, so they live here, out of the synced
  `argo-cd/applications/` path, to make the "hand-applied" boundary obvious.
- Their `source.path` still points at `argo-cd/{applications,helm-repos,crds}`.
  Moving these files did not change those paths, so the live roots reconcile
  exactly the same content as before.
- ArgoCD self-installs from `argocd/` (top-level), driven by
  `argo-cd/applications/argocd-app.yaml`. That is a separate concern from this
  bootstrap seed.
