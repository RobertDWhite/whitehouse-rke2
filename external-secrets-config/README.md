# External Secrets Shared Config

This folder holds shared 1Password integration for all namespaces/apps.

It creates:

- `ClusterSecretStore` named `onepassword-shared`
- Secret `onepassword-sdk-token` in namespace `external-secrets`

Usage:

1. Set the 1Password token in `01-onepassword-token-secret.sops.yaml`.
2. Apply:

```bash
kustomize build --enable-alpha-plugins --enable-exec external-secrets-config | kubectl apply -f -
```

Apps can then reference:

```yaml
spec:
  secretStoreRef:
    kind: ClusterSecretStore
    name: onepassword-shared
```

