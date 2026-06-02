"""
HamDB callsign lookup with local PostgreSQL cache.

Fetches operator info (name, QTH, license class) from hamdb.org for callsigns
found in transcripts, caching results locally to avoid repeated API hits.
Results are used to enrich Ollama tagging prompts with operator context.
"""

import json
from datetime import datetime, timedelta
from typing import Optional
from urllib import error, request

from ..config import settings
from ..models import CallsignInfo

HAMDB_URL = "https://hamdb.org/api/call/{callsign}/json"


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def _fetch_hamdb(callsign: str) -> Optional[dict]:
    url = HAMDB_URL.format(callsign=callsign.upper())
    req = request.Request(url, headers={"User-Agent": "sdr-research/1.0"})
    try:
        with request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
        data = json.loads(body)
        cs = data.get("hamdb", {}).get("callsign", {})
        status = data.get("hamdb", {}).get("messages", {}).get("status", "")
        if status != "OK" or not cs:
            return None
        return cs
    except Exception as exc:
        print(f"[HamDB] Lookup failed for {callsign}: {exc}")
        return None


def _parse_name(cs: dict) -> Optional[str]:
    parts = [cs.get("fname", "").strip(), cs.get("name", "").strip()]
    full = " ".join(p for p in parts if p)
    return full or None


def _parse_float(val: str) -> Optional[float]:
    try:
        f = float(val)
        return f if f != 0.0 else None
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Cache read/write
# ---------------------------------------------------------------------------

def get_cached(db, callsign: str) -> Optional[CallsignInfo]:
    """Return a cached entry if it exists and is within the TTL."""
    row = db.query(CallsignInfo).filter(CallsignInfo.callsign == callsign.upper()).first()
    if row is None:
        return None
    if row.fetched_at is None:
        return None
    age = datetime.utcnow() - row.fetched_at
    if age > timedelta(days=settings.hamdb_cache_days):
        return None
    return row


def store_result(db, callsign: str, cs: Optional[dict]) -> CallsignInfo:
    """Upsert a HamDB result (or a negative cache entry) into callsign_cache."""
    upper = callsign.upper()
    row = db.query(CallsignInfo).filter(CallsignInfo.callsign == upper).first()
    if row is None:
        row = CallsignInfo(callsign=upper)
        db.add(row)

    if cs:
        row.name = _parse_name(cs)
        row.qth_city = cs.get("addr2", "").strip() or None
        row.qth_state = cs.get("state", "").strip() or None
        row.license_class = cs.get("class", "").strip() or None
        row.grid = cs.get("grid", "").strip() or None
        row.latitude = _parse_float(cs.get("lat", ""))
        row.longitude = _parse_float(cs.get("lon", ""))
        row.active = cs.get("status", "").strip().upper() == "A"

    row.fetched_at = datetime.utcnow()
    db.commit()
    return row


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def lookup_callsigns(db, callsigns: list[str], budget: dict[str, int]) -> dict[str, CallsignInfo]:
    """
    Look up a list of callsigns, using cache where available and respecting
    the per-cycle API budget. Returns a dict of callsign → CallsignInfo.
    """
    if not settings.hamdb_enabled:
        return {}

    results: dict[str, CallsignInfo] = {}
    for cs in callsigns:
        upper = cs.upper()
        cached = get_cached(db, upper)
        if cached is not None:
            results[upper] = cached
            continue

        if budget["remaining"] <= 0:
            continue

        budget["remaining"] -= 1
        raw = _fetch_hamdb(upper)
        row = store_result(db, upper, raw)
        if row.name or row.qth_city:
            results[upper] = row

    return results


def callsign_context_str(info_map: dict[str, CallsignInfo]) -> Optional[str]:
    """Build a compact context string for the Ollama prompt."""
    if not info_map:
        return None
    lines = []
    for cs, info in info_map.items():
        parts = [cs]
        if info.name:
            parts.append(info.name)
        if info.qth_city and info.qth_state:
            parts.append(f"{info.qth_city}, {info.qth_state}")
        elif info.qth_state:
            parts.append(info.qth_state)
        if info.license_class:
            cls_map = {"T": "Technician", "G": "General", "A": "Advanced", "E": "Extra"}
            parts.append(cls_map.get(info.license_class.upper(), info.license_class))
        lines.append(" — ".join(parts))
    return "\n".join(lines)
