"""
RepeaterBook integration — periodic sync and per-recording frequency lookup.

Sync fetches open, operational repeaters within a configurable radius of the
station's lat/lon and upserts them into the local `repeaters` table. The
indexer then calls `lookup_repeater()` to match a recording frequency to a
known repeater within ±FREQ_TOLERANCE_HZ.
"""

import asyncio
import json
from datetime import datetime
from typing import Optional
from urllib import error, request

from sqlalchemy import text

from ..config import settings
from ..database import SessionLocal
from ..models import Repeater

FREQ_TOLERANCE_HZ = 6_000   # ±6 kHz — covers FM channel spacing slop
REPEATERBOOK_URL = (
    "https://www.repeaterbook.com/api/export.php"
    "?country=US&state={state}&lat={lat}&lng={lng}"
    "&distance={radius}&Dunit=m&status_id=1&use=OPEN&format=json"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mhz_to_hz(value: str) -> Optional[float]:
    try:
        return float(value) * 1_000_000
    except (ValueError, TypeError):
        return None


def _parse_pl(value: str) -> Optional[float]:
    try:
        f = float(value)
        return f if f > 0 else None
    except (ValueError, TypeError):
        return None


def _digital_modes(row: dict) -> Optional[str]:
    modes = []
    for key in ("DMR", "D-Star", "System Fusion", "P25", "NXDN", "TETRA"):
        if str(row.get(key, "No")).strip().lower() == "yes":
            modes.append(key)
    return ",".join(modes) if modes else None


def _linked_nodes(row: dict) -> Optional[str]:
    parts = []
    if row.get("EchoLink Node", "").strip():
        parts.append(f"EchoLink:{row['EchoLink Node'].strip()}")
    if row.get("IRLP Node", "").strip():
        parts.append(f"IRLP:{row['IRLP Node'].strip()}")
    if row.get("AllStarLink Node", "").strip():
        parts.append(f"AllStar:{row['AllStarLink Node'].strip()}")
    if row.get("WiresX", "").strip():
        parts.append(f"WiresX:{row['WiresX'].strip()}")
    return " ".join(parts) if parts else None


# ---------------------------------------------------------------------------
# Fetch + upsert
# ---------------------------------------------------------------------------

def fetch_repeaterbook() -> list[dict]:
    url = REPEATERBOOK_URL.format(
        state=settings.repeaterbook_state,
        lat=settings.repeaterbook_latitude,
        lng=settings.repeaterbook_longitude,
        radius=settings.repeaterbook_radius_miles,
    )
    req = request.Request(url, headers={"User-Agent": "sdr-research/1.0"})
    try:
        with request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
    except error.URLError as exc:
        print(f"[RepeaterBook] Fetch failed: {exc}")
        return []
    try:
        data = json.loads(body)
        return data.get("results", []) or []
    except Exception as exc:
        print(f"[RepeaterBook] JSON parse failed: {exc}")
        return []


def sync_repeaters():
    if not settings.repeaterbook_enabled:
        return

    print("[RepeaterBook] Syncing repeaters…")
    rows = fetch_repeaterbook()
    if not rows:
        print("[RepeaterBook] No results returned.")
        return

    db = SessionLocal()
    try:
        upserted = 0
        now = datetime.utcnow()
        for row in rows:
            callsign = str(row.get("Call", "")).strip().upper()
            freq_hz = _mhz_to_hz(row.get("Frequency", ""))
            if not callsign or not freq_hz:
                continue

            existing = (
                db.query(Repeater)
                .filter(Repeater.callsign == callsign, Repeater.frequency_hz == freq_hz)
                .first()
            )
            if existing is None:
                existing = Repeater(callsign=callsign, frequency_hz=freq_hz)
                db.add(existing)

            existing.input_hz = _mhz_to_hz(row.get("Input Freq", ""))
            existing.pl_tone = _parse_pl(row.get("PL", ""))
            existing.location = str(row.get("Location", "")).strip() or None
            existing.county = str(row.get("County", "")).strip() or None
            existing.state = str(row.get("ST", row.get("State", ""))).strip() or None
            existing.latitude = _mhz_to_hz(row.get("Latitude", "")) and float(row.get("Latitude", 0)) or None
            existing.longitude = float(row.get("Longitude", 0)) or None
            existing.use = str(row.get("Use", "")).strip() or None
            existing.digital_modes = _digital_modes(row)
            existing.linked_nodes = _linked_nodes(row)
            existing.last_synced = now
            upserted += 1

        db.commit()
        print(f"[RepeaterBook] Upserted {upserted} repeaters.")
    except Exception as exc:
        print(f"[RepeaterBook] Sync error: {exc}")
        db.rollback()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Per-recording lookup
# ---------------------------------------------------------------------------

def lookup_repeater(db, frequency_hz: float) -> Optional[Repeater]:
    """Return the closest repeater whose output frequency is within tolerance."""
    return (
        db.query(Repeater)
        .filter(
            Repeater.frequency_hz >= frequency_hz - FREQ_TOLERANCE_HZ,
            Repeater.frequency_hz <= frequency_hz + FREQ_TOLERANCE_HZ,
        )
        .order_by(
            # Closest frequency first
            text("ABS(frequency_hz - :f)").bindparams(f=frequency_hz)
        )
        .first()
    )


def repeater_label(repeater: Repeater) -> str:
    """Build a short human label for a matched repeater."""
    parts = [f"{repeater.callsign} Rptr"]
    if repeater.location:
        parts.append(repeater.location)
    if repeater.state:
        parts.append(repeater.state)
    return " — ".join(parts)


def repeater_tags(repeater: Repeater) -> list[str]:
    """Return a list of tags to add for a matched repeater."""
    tags = [repeater.callsign]
    if repeater.digital_modes:
        for mode in repeater.digital_modes.split(","):
            tags.append(mode.strip().lower().replace("-", "_").replace(" ", "_"))
    if repeater.linked_nodes:
        for node in repeater.linked_nodes.split():
            prefix = node.split(":")[0].lower()
            tags.append(prefix)
    return tags


# ---------------------------------------------------------------------------
# Background sync loop
# ---------------------------------------------------------------------------

async def run_repeater_sync():
    """Async background task: sync on startup then every N hours."""
    if not settings.repeaterbook_enabled:
        return
    # Initial sync
    await asyncio.to_thread(sync_repeaters)
    interval_sec = settings.repeaterbook_sync_hours * 3600
    while True:
        await asyncio.sleep(interval_sec)
        await asyncio.to_thread(sync_repeaters)
