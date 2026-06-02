#!/usr/bin/env python3
"""
Provision the Authentik Proxy Provider + Application + Outpost binding
for kavita.white.fm, cloning the flow/settings from an existing proxy
provider (default: calibre). Idempotent — safe to re-run.

Usage:
  AUTHENTIK_URL=https://authentik.white.fm \
  AUTHENTIK_TOKEN=...                        \
  ./authentik-provision.py

Optional env:
  TEMPLATE_SLUG   app slug to copy from. Default: calibre.
  DRY_RUN         1 to print what would change without modifying.
  PROVIDER_NAME   default kavita
  APP_SLUG        default kavita
  APP_NAME        default Kavita
  EXTERNAL_HOST   default https://kavita.white.fm
  INTERNAL_HOST   default http://kavita.kavita.svc.cluster.local
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("AUTHENTIK_URL", "").rstrip("/")
TOKEN = os.environ.get("AUTHENTIK_TOKEN", "")
DRY = os.environ.get("DRY_RUN") == "1"
TEMPLATE_SLUG = os.environ.get("TEMPLATE_SLUG", "calibre")
PROVIDER_NAME = os.environ.get("PROVIDER_NAME", "kavita")
APP_SLUG = os.environ.get("APP_SLUG", "kavita")
APP_NAME = os.environ.get("APP_NAME", "Kavita")
EXT = os.environ.get("EXTERNAL_HOST", "https://kavita.white.fm")
INT = os.environ.get("INTERNAL_HOST", "http://kavita.kavita.svc.cluster.local")


def die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


if not BASE or not TOKEN:
    die("AUTHENTIK_URL and AUTHENTIK_TOKEN must be set")


def api(method: str, path: str, body: dict | None = None) -> dict | list:
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {TOKEN}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        die(f"{method} {path} -> HTTP {e.code}: {detail}")
    if not raw:
        return {}
    return json.loads(raw)


def get_paged(path: str, params: dict | None = None) -> list:
    qs = ""
    if params:
        qs = "?" + urllib.parse.urlencode(params)
    results: list = []
    next_url = f"{path}{qs}"
    while next_url:
        data = api("GET", next_url)
        assert isinstance(data, dict)
        results.extend(data.get("results", []))
        # `next` is an absolute URL; strip BASE if present.
        nxt = data.get("next") or ""
        if nxt.startswith(BASE):
            next_url = nxt[len(BASE):]
        elif nxt:
            # Safety net — don't follow external URLs.
            break
        else:
            next_url = ""
    return results


def find_app(slug: str) -> dict | None:
    apps = get_paged("/api/v3/core/applications/", {"slug": slug})
    for a in apps:
        if a.get("slug") == slug:
            return a
    return None


def find_proxy_provider(name: str) -> dict | None:
    provs = get_paged("/api/v3/providers/proxy/", {"name": name})
    for p in provs:
        if p.get("name") == name:
            return p
    return None


def main() -> int:
    print(f"target: {BASE}")
    print(f"template slug: {TEMPLATE_SLUG}")
    print(f"provider name: {PROVIDER_NAME}")
    print(f"app slug: {APP_SLUG}")
    print(f"external host: {EXT}")
    print(f"internal host: {INT}")
    print(f"dry run: {DRY}")
    print()

    # 1. Fetch template application + its proxy provider.
    template_app = find_app(TEMPLATE_SLUG)
    if not template_app:
        die(f"template app not found: slug={TEMPLATE_SLUG}")
    template_provider_pk = template_app.get("provider")
    if not template_provider_pk:
        die(f"template app '{TEMPLATE_SLUG}' has no provider")

    template_provider = api("GET", f"/api/v3/providers/proxy/{template_provider_pk}/")
    assert isinstance(template_provider, dict)
    print(f"copying flows from template provider: {template_provider.get('name')}")

    auth_flow = template_provider.get("authorization_flow")
    inval_flow = template_provider.get("invalidation_flow")
    mode = template_provider.get("mode", "forward_single")
    cookie_domain = template_provider.get("cookie_domain", "")
    access_token_validity = template_provider.get(
        "access_token_validity", "hours=24"
    )
    refresh_token_validity = template_provider.get(
        "refresh_token_validity", "days=30"
    )

    # 2. Create or update the kavita proxy provider.
    desired_provider = {
        "name": PROVIDER_NAME,
        "authorization_flow": auth_flow,
        "invalidation_flow": inval_flow,
        "external_host": EXT,
        "internal_host": INT,
        "internal_host_ssl_validation": False,
        "mode": mode,
        "cookie_domain": cookie_domain,
        "access_token_validity": access_token_validity,
        "refresh_token_validity": refresh_token_validity,
    }

    existing = find_proxy_provider(PROVIDER_NAME)
    if existing:
        pk = existing["pk"]
        print(f"provider '{PROVIDER_NAME}' exists (pk={pk}); PATCH")
        if not DRY:
            api("PATCH", f"/api/v3/providers/proxy/{pk}/", desired_provider)
    else:
        print(f"provider '{PROVIDER_NAME}' does not exist; POST")
        if not DRY:
            created = api("POST", "/api/v3/providers/proxy/", desired_provider)
            assert isinstance(created, dict)
            existing = created
            pk = created["pk"]
        else:
            pk = -1

    # 3. Create or update the application.
    desired_app = {
        "name": APP_NAME,
        "slug": APP_SLUG,
        "provider": pk,
        "policy_engine_mode": template_app.get("policy_engine_mode", "any"),
        "meta_launch_url": EXT,
    }
    existing_app = find_app(APP_SLUG)
    if existing_app:
        print(f"app '{APP_SLUG}' exists; PATCH")
        if not DRY:
            api("PATCH", f"/api/v3/core/applications/{APP_SLUG}/", desired_app)
    else:
        print(f"app '{APP_SLUG}' does not exist; POST")
        if not DRY:
            api("POST", "/api/v3/core/applications/", desired_app)

    # 4. Find the outpost(s) that currently embed the template provider,
    # and add our new provider to the same outposts.
    outposts = get_paged("/api/v3/outposts/instances/")
    template_outposts = [
        o for o in outposts if template_provider_pk in (o.get("providers") or [])
    ]
    if not template_outposts:
        print(
            f"warning: no outpost hosts the template provider "
            f"(pk={template_provider_pk}); provider created but NOT bound.",
            file=sys.stderr,
        )
    for op in template_outposts:
        op_pk = op["pk"]
        op_name = op.get("name", op_pk)
        providers = list(op.get("providers") or [])
        if pk in providers:
            print(f"outpost '{op_name}' already has provider {pk}")
            continue
        providers.append(pk)
        print(f"adding provider {pk} to outpost '{op_name}'")
        if not DRY:
            api(
                "PATCH",
                f"/api/v3/outposts/instances/{op_pk}/",
                {"providers": providers},
            )

    print()
    print("done. Test: curl -Ik " + EXT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
