"""Congress.gov legislative context for recently active traders.

This is context, not causality: it stores recent sponsored legislation for members who appear in
recent trades, then the API can show nearby policy activity next to committee/ticker exposure.
Set CONGRESS_GOV_API_KEY when available; the job skips cleanly if Congress.gov rejects unauthenticated
traffic.
"""
import datetime as dt
import os
import time

from sqlalchemy import and_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import load_config
from app.db import SessionLocal, init_db
from app.models import LegislativeEvent, Member, Trade

from . import common


def _parse_dt(value):
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            return dt.datetime.combine(dt.date.fromisoformat(value[:10]), dt.time(), dt.timezone.utc)
        except ValueError:
            return None


def _event_from_bill(member, bill):
    congress = bill.get("congress")
    typ = (bill.get("type") or "").lower()
    num = bill.get("number")
    ext = f"bill:{congress}:{typ}:{num}:{member.bioguide}"
    url = bill.get("url") or (f"https://www.congress.gov/bill/{congress}th-congress/{typ}/{num}" if congress and typ and num else None)
    title = bill.get("title") or bill.get("latestTitle")
    return {
        "source": "congress.gov",
        "event_type": "bill",
        "congress": int(congress) if congress else None,
        "chamber": member.chamber,
        "bioguide": member.bioguide,
        "member_id": member.id,
        "committee": None,
        "sector": (member.committee_sectors or [None])[0],
        "title": title,
        "url": url,
        "occurred_at": _parse_dt(bill.get("introducedDate") or bill.get("updateDate")),
        "external_id": ext,
        "payload": bill,
    }


def run():
    cfg = load_config()
    init_db()
    cc = cfg.get("congress_gov", {})
    if not cc.get("enabled", True):
        return
    base = cc.get("base_url", "https://api.congress.gov/v3").rstrip("/")
    lookback = int(cc.get("lookback_days", 14))
    cap = int(cc.get("max_members_per_run", 80))
    key = os.environ.get("CONGRESS_GOV_API_KEY")
    sess = common.make_session(cfg)
    db = SessionLocal()
    stored = 0
    failures = 0
    try:
        since = dt.date.today() - dt.timedelta(days=lookback)
        members = db.scalars(
            select(Member)
            .join(Trade, Trade.member_id == Member.id)
            .where(and_(Member.bioguide.isnot(None), Trade.disclosure_date >= since))
            .group_by(Member.id)
            .limit(cap)
        ).all()
        if not members:
            common.record_run(db, "legislative_events", rows_upserted=0, success=True, note="no recently active bioguides")
            return

        for member in members:
            params = {"format": "json", "limit": 20}
            if key:
                params["api_key"] = key
            url = f"{base}/member/{member.bioguide}/sponsored-legislation"
            r = sess.get(url, params=params, timeout=30)
            if r.status_code >= 400:
                print(f"legislative: {member.bioguide} HTTP {r.status_code}")
                failures += 1
                time.sleep(1)
                continue
            for bill in (r.json().get("sponsoredLegislation") or r.json().get("bills") or []):
                event = _event_from_bill(member, bill)
                db.execute(
                    pg_insert(LegislativeEvent)
                    .values(**event)
                    .on_conflict_do_update(
                        index_elements=["external_id"],
                        set_={k: v for k, v in event.items() if k != "external_id"},
                    )
                )
                stored += 1
            if stored % 50 == 0:
                db.commit()
            time.sleep(0.25)
        db.commit()
        note = f"{failures} member fetches failed" if failures else None
        common.record_run(db, "legislative_events", rows_upserted=stored, success=(stored > 0 or failures == 0), note=note)
        print(f"legislative: stored {stored} events")
    except Exception as e:  # noqa: BLE001
        common.record_run(db, "legislative_events", success=False, note=str(e))
        raise
    finally:
        db.close()


if __name__ == "__main__":
    run()
