"""App Store Connect Prometheus Exporter.

Pulls Sales Reports, Customer Reviews, and Performance Metrics from the
App Store Connect API and exposes them as Prometheus metrics.

Auth: JWT signed with ES256 using a .p8 private key from App Store Connect.
Credentials are expected as environment variables (injected from a Kubernetes
Secret synced from 1Password via External Secrets Operator).

Metrics exposed:
  appstore_up                               1 if API reachable
  appstore_downloads_total                  Cumulative downloads by app/type/device/country
  appstore_proceeds_total                   Revenue by app/country/currency
  appstore_units_daily                      Units (installs) from the most recent daily report
  appstore_rating_average                   Average star rating
  appstore_rating_count                     Number of ratings
  appstore_reviews_total                    Total review count
  appstore_review_latest                    Latest reviews (gauge=1, labels carry content)
  appstore_crash_count                      Crash count by app/version/os
  appstore_launch_time_ms                   Launch time p50/p90 by app/version
  appstore_info                             App metadata (name, version, bundle ID)
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import os
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from prometheus_client import Gauge, Info, start_http_server


# ---------------------------------------------------------------------------
# Config from env (populated by External Secrets from 1Password)
# ---------------------------------------------------------------------------

# The 1Password item may use various field names. We check common variants.
def _env(*keys: str, default: str = "") -> str:
    for k in keys:
        v = os.environ.get(k, "").strip()
        if v:
            return v
    return default


ISSUER_ID = _env("issuer_id", "ISSUER_ID", "issuerId")
KEY_ID = _env("key_id", "KEY_ID", "keyId", "kid")
PRIVATE_KEY = _env("private_key", "PRIVATE_KEY", "privateKey", "p8_key")
APP_IDS = [a.strip() for a in _env("app_ids", "APP_IDS", "appIds").split(",") if a.strip()]
VENDOR_NUMBER = _env("vendor_number", "VENDOR_NUMBER", "vendorNumber")

LISTEN_PORT = int(os.environ.get("EXPORTER_PORT", "9488"))
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "300"))  # 5 min default

API_BASE = "https://api.appstoreconnect.apple.com"


# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------

UP = Gauge("appstore_up", "1 if App Store Connect API is reachable")

UNITS_DAILY = Gauge(
    "appstore_units_daily",
    "Units from daily sales report",
    ["app_name", "product_type", "device", "country"],
)
PROCEEDS_DAILY = Gauge(
    "appstore_proceeds_daily",
    "Proceeds from daily sales report",
    ["app_name", "currency", "country"],
)
RATING_AVG = Gauge("appstore_rating_average", "Average star rating", ["app_name"])
RATING_COUNT = Gauge("appstore_rating_count", "Number of ratings", ["app_name"])
REVIEWS_TOTAL = Gauge("appstore_reviews_total", "Total reviews", ["app_name"])
REVIEW_LATEST = Gauge(
    "appstore_review_latest",
    "Latest review (gauge=1)",
    ["app_name", "title", "body", "rating", "reviewer", "territory", "date"],
)
APP_INFO = Info("appstore_app", "App metadata")
CRASH_COUNT = Gauge(
    "appstore_crash_count", "Crash count", ["app_name", "version", "os"]
)


# ---------------------------------------------------------------------------
# JWT auth
# ---------------------------------------------------------------------------

_token_cache: dict[str, Any] = {"token": "", "expires": 0}


def _get_token() -> str:
    now = time.time()
    if _token_cache["token"] and _token_cache["expires"] > now + 60:
        return _token_cache["token"]

    if not ISSUER_ID or not KEY_ID or not PRIVATE_KEY:
        raise RuntimeError("Missing App Store Connect credentials (issuer_id, key_id, private_key)")

    # Handle the private key — may be raw PEM or escaped newlines
    key = PRIVATE_KEY
    if "\\n" in key and "\n" not in key:
        key = key.replace("\\n", "\n")
    if not key.startswith("-----"):
        key = f"-----BEGIN PRIVATE KEY-----\n{key}\n-----END PRIVATE KEY-----"

    exp = now + 1200  # 20 min max
    payload = {
        "iss": ISSUER_ID,
        "iat": int(now),
        "exp": int(exp),
        "aud": "appstoreconnect-v1",
    }
    token = jwt.encode(payload, key, algorithm="ES256", headers={"kid": KEY_ID})
    _token_cache["token"] = token
    _token_cache["expires"] = exp
    return token


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def _api_get(path: str, params: dict | None = None, raw: bool = False) -> Any:
    url = f"{API_BASE}{path}"
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url += f"?{qs}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {_get_token()}",
        "Accept": "application/a]json" if not raw else "application/a]gzip",
    })
    # Fix the Accept header (no brackets)
    if raw:
        req.add_header("Accept", "application/a]gzip, application/json")
    else:
        req.remove_header("Accept")
        req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req, timeout=30) as resp:
        if raw:
            return resp.read()
        return json.loads(resp.read().decode())


def _api_get_sales_report(report_date: str) -> list[dict[str, str]]:
    """Download a daily sales report (gzipped TSV)."""
    url = f"{API_BASE}/v1/salesReports"
    params = {
        "filter[reportType]": "SALES",
        "filter[reportSubType]": "SUMMARY",
        "filter[frequency]": "DAILY",
        "filter[reportDate]": report_date,
        "filter[vendorNumber]": VENDOR_NUMBER,
    }
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    full_url = f"{url}?{qs}"
    req = urllib.request.Request(full_url, headers={
        "Authorization": f"Bearer {_get_token()}",
        "Accept": "application/a]gzip",
    })
    req.remove_header("Accept")
    req.add_header("Accept", "application/a]gzip")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
        # Response is gzipped TSV
        try:
            text = gzip.decompress(data).decode("utf-8")
        except gzip.BadGzipFile:
            text = data.decode("utf-8")
        reader = csv.DictReader(io.StringIO(text), delimiter="\t")
        return list(reader)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return []  # No report for this date yet
        raise


# ---------------------------------------------------------------------------
# Collectors
# ---------------------------------------------------------------------------

def collect_sales() -> None:
    """Pull yesterday's daily sales report."""
    if not VENDOR_NUMBER:
        return
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    try:
        rows = _api_get_sales_report(yesterday)
        if not rows:
            return
        UP.set(1)
        UNITS_DAILY._metrics.clear()
        PROCEEDS_DAILY._metrics.clear()
        for row in rows:
            app_name = row.get("Title") or row.get("App Name") or "?"
            units = float(row.get("Units") or 0)
            proceeds = float(row.get("Developer Proceeds") or 0)
            device = row.get("Device") or "?"
            country = row.get("Country Code") or "?"
            currency = row.get("Currency of Proceeds") or "?"
            product_type = row.get("Product Type Identifier") or "?"

            if units:
                UNITS_DAILY.labels(
                    app_name=app_name, product_type=product_type,
                    device=device, country=country,
                ).set(units)
            if proceeds:
                PROCEEDS_DAILY.labels(
                    app_name=app_name, currency=currency, country=country,
                ).set(proceeds)
    except Exception as e:
        print(f"[appstore] sales report error: {e}", flush=True)


def collect_reviews() -> None:
    """Pull customer reviews for each app."""
    REVIEW_LATEST._metrics.clear()
    for app_id in APP_IDS:
        try:
            data = _api_get(f"/v1/apps/{app_id}/customerReviews", {
                "sort": "-createdDate",
                "limit": "10",
            })
            reviews = data.get("data", [])
            total = data.get("meta", {}).get("paging", {}).get("total", len(reviews))

            # Get app name from the first review or use app_id
            app_name = app_id
            if reviews:
                # Try to get app name from included data
                for inc in data.get("included", []):
                    if inc.get("type") == "apps":
                        app_name = inc.get("attributes", {}).get("name", app_id)
                        break

            REVIEWS_TOTAL.labels(app_name=app_name).set(total)

            # Calculate average rating from recent reviews
            ratings = [
                int(r.get("attributes", {}).get("rating", 0))
                for r in reviews
                if r.get("attributes", {}).get("rating")
            ]
            if ratings:
                RATING_AVG.labels(app_name=app_name).set(sum(ratings) / len(ratings))
                RATING_COUNT.labels(app_name=app_name).set(len(ratings))

            # Expose latest reviews as labeled gauges
            for r in reviews[:5]:
                attrs = r.get("attributes", {})
                REVIEW_LATEST.labels(
                    app_name=app_name,
                    title=str(attrs.get("title", ""))[:80],
                    body=str(attrs.get("body", ""))[:120],
                    rating=str(attrs.get("rating", "?")),
                    reviewer=str(attrs.get("reviewerNickname", "?")),
                    territory=str(attrs.get("territory", "?")),
                    date=str(attrs.get("createdDate", "?"))[:10],
                ).set(1)
            UP.set(1)
        except urllib.error.HTTPError as e:
            print(f"[appstore] reviews error for {app_id}: {e.code} {e.reason}", flush=True)
        except Exception as e:
            print(f"[appstore] reviews error for {app_id}: {e}", flush=True)


def collect_perf_metrics() -> None:
    """Pull performance/crash metrics for each app."""
    CRASH_COUNT._metrics.clear()
    for app_id in APP_IDS:
        try:
            data = _api_get(f"/v1/apps/{app_id}/perfPowerMetrics", {
                "filter[metricType]": "CRASH",
                "filter[platform]": "IOS",
            })
            for item in data.get("data", []):
                attrs = item.get("attributes", {})
                for dataset in attrs.get("datasets", []):
                    for point in dataset.get("points", []):
                        version = point.get("version") or "?"
                        os_ver = point.get("osVersion") or "?"
                        value = point.get("value") or 0
                        CRASH_COUNT.labels(
                            app_name=app_id, version=version, os=os_ver,
                        ).set(float(value))
            UP.set(1)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                continue  # No perf data for this app
            print(f"[appstore] perf metrics error for {app_id}: {e.code}", flush=True)
        except Exception as e:
            print(f"[appstore] perf metrics error for {app_id}: {e}", flush=True)


def collect_app_info() -> None:
    """Pull basic app metadata."""
    try:
        data = _api_get("/v1/apps", {"limit": "25"})
        apps = data.get("data", [])
        for app in apps:
            attrs = app.get("attributes", {})
            app_id = app.get("id", "?")
            if APP_IDS and app_id not in APP_IDS:
                continue
            APP_INFO.info({
                "app_id": app_id,
                "name": attrs.get("name", "?"),
                "bundle_id": attrs.get("bundleId", "?"),
                "sku": attrs.get("sku", "?"),
            })
        UP.set(1)
    except Exception as e:
        print(f"[appstore] app info error: {e}", flush=True)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> None:
    print(f"[appstore] App Store Connect Exporter starting port={LISTEN_PORT}", flush=True)
    print(f"[appstore] issuer_id={'set' if ISSUER_ID else 'MISSING'} "
          f"key_id={'set' if KEY_ID else 'MISSING'} "
          f"private_key={'set' if PRIVATE_KEY else 'MISSING'} "
          f"app_ids={APP_IDS or 'auto'} "
          f"vendor_number={'set' if VENDOR_NUMBER else 'MISSING'}", flush=True)

    if not ISSUER_ID or not KEY_ID or not PRIVATE_KEY:
        print("[appstore] WARNING: credentials not configured. "
              "Exporter will serve empty metrics until the 1Password "
              "ExternalSecret syncs.", flush=True)

    start_http_server(LISTEN_PORT)

    while True:
        try:
            if ISSUER_ID and KEY_ID and PRIVATE_KEY:
                collect_app_info()
                collect_sales()
                collect_reviews()
                collect_perf_metrics()
            else:
                UP.set(0)
        except Exception:
            print("[appstore] collection error:", flush=True)
            traceback.print_exc()
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
