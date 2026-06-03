# bootstrap/

Standing up the cluster from bare metal. These are the **only** things applied
by hand — after this, ArgoCD reconciles the entire tree from git.

> Heads-up: the ArgoCD install chart itself lives at the repo-root `argocd/`
> (not under here), because ArgoCD self-manages that path via
> `argo-cd/applications/argocd-app.yaml`. This README is the runbook that ties
> the install + the roots together. See "Why argocd/ is not in bootstrap/".

## Prerequisites

- `kubectl` pointed at the fresh cluster, plus `kustomize`, `helm`, `sops`, and
  the `ksops` exec plugin on your PATH.
- **The age private key** (`argo-cd/keys.txt`, gitignored — never committed).
  Everything in this repo is SOPS/age-encrypted, *including the cluster's own
  copy of the age key*, so the very first render must decrypt locally with this
  file. Without it, nothing comes up.

## Sequence

```sh
export SOPS_AGE_KEY_FILE="$PWD/argo-cd/keys.txt"

# 1. Install ArgoCD (+ its sops-age and git-repo-cred secrets, via KSOPS).
#    KSOPS is an exec generator, so this must render locally with the age key.
kustomize build --enable-alpha-plugins --enable-exec --enable-helm argocd/ \
  | kubectl apply -f -

# 2. Wait for the argocd namespace to be Ready, then seed the app-of-apps roots.
kubectl apply -f bootstrap/app-of-apps.yaml   # → argo-cd/applications/ (99 apps + 4 AppProjects)
kubectl apply -f bootstrap/app-of-repos.yaml  # → argo-cd/helm-repos/
kubectl apply -f bootstrap/app-of-crds.yaml   # → argo-cd/crds/
```

From here ArgoCD takes over: `argocd-app.yaml` (one of the 99) adopts the
`argocd/` install for self-management, `app-of-crds` installs cluster CRDs,
`app-of-repos` registers Helm repos, and the four AppProjects (`platform`,
`security`, `observability`, `apps`) scope everything else. Nothing else is ever
applied by hand.

## Why argocd/ is not in bootstrap/

The install dir does double duty: it's both the thing you `apply` by hand in
step 1 **and** the path `argo-cd/applications/argocd-app.yaml` watches for
self-management (`prune: true`). Relocating it under `bootstrap/argocd/` would
require flipping that live Application's `path` in lockstep with the move —
otherwise ArgoCD would look at the old path, find nothing, and prune its own
install. So it stays at the repo root, and this runbook references it.

## What's in here

| file | applied | manages |
|---|---|---|
| `app-of-apps.yaml`  | by hand, once | `argo-cd/applications/` — 99 Applications + 4 AppProjects |
| `app-of-repos.yaml` | by hand, once | `argo-cd/helm-repos/` — Helm repository registrations |
| `app-of-crds.yaml`  | by hand, once | `argo-cd/crds/` — cluster CRDs |

None of the three is managed by any Application (no `app-of-apps` tracking id) —
they are the seed. Their `source.path` still points at `argo-cd/{...}`; moving
these files into `bootstrap/` did not change those paths, so the live roots
reconcile identical content.
