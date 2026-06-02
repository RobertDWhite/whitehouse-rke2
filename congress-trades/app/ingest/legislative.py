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
from . import taxonomy


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


def _event_from_context(row, event_type, congress, chamber):
    title = row.get("title") or row.get("name") or row.get("description") or row.get("question")
    committee = row.get("committeeName") or row.get("committee") or row.get("committees")
    if isinstance(committee, list):
        committee = ", ".join(str(c.get("name") or c) for c in committee[:2])
    sectors = taxonomy.committee_sectors([committee]) if committee else []
    ext_id = (
        row.get("eventId")
        or row.get("jacketNumber")
        or row.get("rollCallNumber")
        or row.get("number")
        or row.get("url")
        or title
    )
    occurred = (
        row.get("date")
        or row.get("startDate")
        or row.get("meetingDate")
        or row.get("voteDate")
        or row.get("updateDate")
    )
    return {
        "source": "congress.gov",
        "event_type": event_type,
        "congress": int(congress) if congress else None,
        "chamber": chamber,
        "bioguide": None,
        "member_id": None,
        "committee": committee,
        "sector": sectors[0] if sectors else None,
        "title": title,
        "url": row.get("url") or row.get("congressdotgovUrl"),
        "occurred_at": _parse_dt(occurred),
        "external_id": f"{event_type}:{congress}:{chamber}:{ext_id}",
        "payload": row,
    }


def _vote_session(row):
    return row.get("sessionNumber") or row.get("session") or row.get("session_number")


def _vote_number(row):
    return row.get("rollCallNumber") or row.get("rollCallVoteNumber") or row.get("voteNumber") or row.get("number")


def _member_vote_bioguide(row):
    member = row.get("member") if isinstance(row.get("member"), dict) else {}
    return (
        row.get("bioguideId")
        or row.get("bioguideID")
        or row.get("bioguide")
        or row.get("memberId")
        or member.get("bioguideId")
        or member.get("bioguideID")
        or member.get("bioguide")
    )


def _member_vote_value(row):
    return row.get("voteCast") or row.get("vote") or row.get("position") or row.get("cast")


def _event_from_member_vote(row, vote_row, member, congress):
    session = _vote_session(vote_row)
    number = _vote_number(vote_row)
    vote = _member_vote_value(row)
    vote_title = vote_row.get("question") or vote_row.get("title") or vote_row.get("description") or f"House vote {number}"
    occurred = vote_row.get("date") or vote_row.get("voteDate") or vote_row.get("updateDate")
    return {
        "source": "congress.gov",
        "event_type": "member_house_vote",
        "congress": int(congress) if congress else None,
        "chamber": "house",
        "bioguide": member.bioguide,
        "member_id": member.id,
        "committee": None,
        "sector": (member.committee_sectors or [None])[0],
        "title": f"{member.full_name} voted {vote or '?'} on {vote_title}",
        "url": vote_row.get("url") or vote_row.get("congressdotgovUrl"),
        "occurred_at": _parse_dt(occurred),
        "external_id": f"member_house_vote:{congress}:{session}:{number}:{member.bioguide}",
        "payload": {"vote": vote_row, "member_vote": row},
    }


def _fetch_list(sess, url, params, keys):
    r = sess.get(url, params=params, timeout=30)
    if r.status_code >= 400:
        print(f"legislative: {url} HTTP {r.status_code}")
        return []
    j = r.json()
    for key in keys:
        if isinstance(j.get(key), list):
            return j[key]
    return []


def _store_event(db, event):
    db.execute(
        pg_insert(LegislativeEvent)
        .values(**event)
        .on_conflict_do_update(
            index_elements=["external_id"],
            set_={k: v for k, v in event.items() if k != "external_id"},
        )
    )


def run():
    cfg = load_config()
    init_db()
    cc = cfg.get("congress_gov", {})
    if not cc.get("enabled", True):
        return
    base = cc.get("base_url", "https://api.congress.gov/v3").rstrip("/")
    lookback = int(cc.get("lookback_days", 14))
    cap = int(cc.get("max_members_per_run", 80))
    congress = int(cc.get("congress", 119))
    max_votes = int(cc.get("max_votes_per_run", 25))
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
                _store_event(db, _event_from_bill(member, bill))
                stored += 1
            if stored % 50 == 0:
                db.commit()
            time.sleep(0.25)

        params = {"format": "json", "limit": 100}
        if key:
            params["api_key"] = key
        for chamber in ("house", "senate"):
            for row in _fetch_list(
                sess,
                f"{base}/committee-meeting/{congress}/{chamber}",
                params,
                ("committeeMeetings", "meetings", "committeeMeeting"),
            ):
                _store_event(db, _event_from_context(row, "committee_meeting", congress, chamber))
                stored += 1
            for row in _fetch_list(
                sess,
                f"{base}/hearing/{congress}/{chamber}",
                params,
                ("hearings", "hearing"),
            ):
                _store_event(db, _event_from_context(row, "hearing", congress, chamber))
                stored += 1
            time.sleep(0.25)

        active_by_bioguide = {m.bioguide: m for m in members if m.bioguide}
        house_votes = _fetch_list(
            sess,
            f"{base}/house-vote/{congress}",
            params,
            ("houseVotes", "votes", "houseVote"),
        )
        for row in house_votes:
            _store_event(db, _event_from_context(row, "house_vote", congress, "house"))
            stored += 1
        for row in house_votes[:max_votes]:
            session = _vote_session(row)
            number = _vote_number(row)
            if not session or not number:
                continue
            member_rows = _fetch_list(
                sess,
                f"{base}/house-vote/{congress}/{session}/{number}/members",
                params,
                ("houseRollCallVoteMemberVotes", "memberVotes", "members", "votes"),
            )
            for member_vote in member_rows:
                bioguide = _member_vote_bioguide(member_vote)
                member = active_by_bioguide.get(bioguide)
                if not member:
                    continue
                _store_event(db, _event_from_member_vote(member_vote, row, member, congress))
                stored += 1
            time.sleep(0.2)
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
