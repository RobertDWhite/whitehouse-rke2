# Re-create the Technitium SSO config

Technitium 15.1 has native OIDC. Authentik is the IdP. Two API surfaces:

- **Authentik**: create an OAuth2/OpenID Provider + Application bound to the
  Embedded Outpost. Owns the client_id / client_secret / redirect URIs.
- **Technitium**: `POST /api/admin/sso/set` with the resolved client info.

The credentials are stored in `30-secret.sops.yaml` (`sso-client-id`,
`sso-client-secret`, `sso-authority`) so this can be re-applied without
clicking through the UI.

## Recreate from scratch

```bash
kubectl -n authentik port-forward svc/authentik-server 9444:80 >/dev/null 2>&1 &
PF=$!; sleep 2
TOKEN=$(sops -d authentik/authentik-env.sops.yaml | awk -F': ' '/AUTHENTIK_BOOTSTRAP_TOKEN/ {print $2}')

# Find an existing OAuth2/OpenID provider (e.g. Grafana) to copy auth flow + property mapping UUIDs from.
curl -sk -H "Authorization: Bearer $TOKEN" "http://localhost:9444/api/v3/providers/oauth2/?search=Grafana" \
  | python3 -c "import json,sys; p=json.load(sys.stdin)['results'][0]; print('authz', p['authorization_flow']); print('inval', p['invalidation_flow']); print('mappings', p['property_mappings'])"

# Find a signing cert
curl -sk -H "Authorization: Bearer $TOKEN" "http://localhost:9444/api/v3/crypto/certificatekeypairs/?has_key=true" \
  | python3 -c "import json,sys; [print(c['pk'],c['name']) for c in json.load(sys.stdin)['results']]"

kill $PF
```

Then `POST /api/v3/providers/oauth2/` with:
- `name: Provider for Technitium SSO`
- `client_type: confidential`
- `redirect_uris`: strict matches for `https://technitium.internal.white.fm/sso/callback`
  and `https://dns.internal.white.fm/sso/callback`
- `signing_key`: pk from above
- `authorization_flow`, `invalidation_flow`, `property_mappings`: from above
- `sub_mode: hashed_user_id`, `include_claims_in_id_token: true`,
  `issuer_mode: per_provider`

Capture `client_id` and `client_secret` from the response (they're auto-generated).
The issuer URL becomes `https://auth.white.fm/application/o/<application-slug>/`.

Then `POST /api/v3/core/applications/` with `slug: technitium-sso`,
`provider: <new pk>`, `meta_launch_url: https://technitium.internal.white.fm`.

Then push the secrets back into Technitium:

```bash
PASS=$(sops -d technitium/30-secret.sops.yaml | awk -F': ' '/admin-password/ {print $2}')
CID=$(sops -d technitium/30-secret.sops.yaml | awk -F': ' '/sso-client-id/ {print $2}')
CSEC=$(sops -d technitium/30-secret.sops.yaml | awk -F': ' '/sso-client-secret/ {print $2}')
ISSUER=$(sops -d technitium/30-secret.sops.yaml | awk -F': ' '/sso-authority/ {print $2}')

kubectl -n technitium exec technitium-0 -c zone-importer -- env P="$PASS" CID="$CID" CSEC="$CSEC" ISS="$ISSUER" python -c "
import os, requests
tok = requests.get('http://localhost:5380/api/user/login', params={'user':'admin','pass':os.environ['P'],'includeInfo':'true'}, timeout=10).json()['token']
r = requests.get('http://localhost:5380/api/admin/sso/set', params={
    'token':tok,'ssoEnabled':'true',
    'ssoAuthority':os.environ['ISS'],'ssoClientId':os.environ['CID'],'ssoClientSecret':os.environ['CSEC'],
    'ssoScopes':'openid|profile|email','ssoAllowSignup':'true','ssoAllowSignupOnlyForMappedUsers':'true',
    'ssoGroupMap':'authentik Admins|Administrators',
}, timeout=15)
print(r.json().get('response',{}).get('ssoEnabled'))
"
```

## Group mapping

`authentik Admins` → `Administrators` is set by default. To map more groups,
re-run the `/api/admin/sso/set` call with `ssoGroupMap` repeated for each
mapping (form: `<authentik group name>|<technitium group name>`).
