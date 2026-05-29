"""Hourly third-party top-up from Lambda Finance (free, no API key).

This is a freshness convenience layer only — the House/Senate primary parsers are the
source of truth and supersede these rows (see SOURCE_PRIORITY in common.py)."""
import time

from app.config import load_config
from app.db import SessionLocal, init_db

from . import common
from . import normalize as nz


def run():
    cfg = load_config()
    init_db()
    sess = common.make_session(cfg)
    lc = cfg["lambda"]
    params = {"days": min(int(lc.get("days", 365)), 730), "limit": lc.get("limit", 1000)}

    trades = []
    for attempt in range(4):
        r = sess.get(lc["url"], params=params, timeout=60)
        r.raise_for_status()
        j = r.json()
        trades = j.get("trades") or j.get("data") or []
        if trades:
            break
        print(f"lambda: empty response (attempt {attempt + 1}), retrying")
        time.sleep(3)

    if not trades:
        print("lambda: no trades returned after retries")
        return

    db = SessionLocal()
    n = 0
    try:
        for row in trades:
            chamber = (row.get("chamber") or "").lower() or None
            name = row.get("representative") or row.get("senator") or row.get("name") or ""
            member = common.get_or_create_member(
                db,
                name,
                chamber=chamber,
                party=row.get("party"),
                state=row.get("state"),
                district=row.get("district"),
            )
            if not member:
                continue
            amount = row.get("amount")
            lo, hi, raw = nz.parse_amount(amount)
            common.upsert_trade(
                db,
                source="lambda",
                member=member,
                chamber=chamber,
                transaction_date=nz.parse_date(row.get("transactionDate")),
                disclosure_date=nz.parse_date(row.get("disclosureDate")),
                owner=row.get("owner"),
                ticker=row.get("symbol"),
                asset_name=row.get("assetDescription"),
                asset_type=None,
                transaction_type=nz.norm_tx_type(row.get("type")),
                amount_min=lo,
                amount_max=hi,
                amount_range_raw=raw or (str(amount) if amount else None),
                cap_gains_over_200=row.get("capGainsOver200"),
                comment=row.get("comment"),
            )
            n += 1
        db.commit()
        print(f"lambda: upserted {n} trades")
    finally:
        db.close()


if __name__ == "__main__":
    run()
