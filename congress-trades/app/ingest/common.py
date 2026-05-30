import datetime as dt

import requests
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models import Filing, IngestState, Member, Trade

from . import normalize as nz

SOURCE_PRIORITY = {"lambda": 1, "house_primary": 2, "senate_primary": 2}


def get_ingest_state(session, source):
    st = session.get(IngestState, source)
    if not st:
        st = IngestState(source=source)
        session.add(st)
        session.flush()
    return st


def record_run(session, source, rows_upserted=0, success=True, note=None):
    """Track per-source run outcome so the API can expose a freshness/watchdog metric."""
    st = get_ingest_state(session, source)
    now = dt.datetime.now(dt.timezone.utc)
    st.last_run = now
    if success:
        st.last_success = now
        st.rows_upserted = rows_upserted
    if note is not None:
        st.note = note[:500]
    session.commit()

_TRADE_COLS = [
    "filing_id",
    "source",
    "source_priority",
    "member_id",
    "chamber",
    "transaction_date",
    "disclosure_date",
    "owner",
    "ticker",
    "asset_name",
    "asset_type",
    "transaction_type",
    "amount_min",
    "amount_max",
    "amount_range_raw",
    "cap_gains_over_200",
    "comment",
]


def make_session(cfg):
    s = requests.Session()
    s.headers.update({"User-Agent": cfg.get("user_agent", "whitehouse-congress/1.0")})
    return s


def get_or_create_member(session, full_name, chamber=None, party=None, state=None, district=None):
    nn = nz.norm_name(full_name or "")
    if not nn:
        return None
    m = session.scalar(select(Member).where(Member.name_norm == nn))
    if m:
        if chamber and not m.chamber:
            m.chamber = chamber
        if party and not m.party:
            m.party = party
        if state and not m.state:
            m.state = state
        if district and not m.district:
            m.district = district
        return m
    m = Member(
        full_name=(full_name or "").strip(),
        name_norm=nn,
        chamber=chamber,
        party=party,
        state=state,
        district=district,
    )
    session.add(m)
    session.flush()
    return m


def get_filing(session, source, doc_id):
    return session.scalar(
        select(Filing).where(Filing.source == source, Filing.doc_id == doc_id)
    )


def upsert_trade(
    session,
    *,
    source,
    member,
    chamber,
    transaction_date,
    disclosure_date,
    owner,
    ticker,
    asset_name,
    asset_type,
    transaction_type,
    amount_min,
    amount_max,
    amount_range_raw,
    cap_gains_over_200=None,
    comment=None,
    filing_id=None,
):
    name_norm = member.name_norm if member else ""
    ticker = nz.clean_ticker(ticker)
    # Guard OCR/parse year-mangling (e.g. 3031, 2220): a transaction is disclosed AFTER it happens,
    # so a date in the future or past its own disclosure is corrupt. Drop the date, keep the trade.
    today = dt.date.today()
    if transaction_date and (transaction_date > today or (disclosure_date and transaction_date > disclosure_date)):
        transaction_date = None
    if disclosure_date and disclosure_date > today:
        disclosure_date = None
    key = nz.dedup_key(
        chamber, name_norm, transaction_date, ticker, amount_min, amount_max, transaction_type
    )
    prio = SOURCE_PRIORITY.get(source, 1)

    stmt = pg_insert(Trade).values(
        filing_id=filing_id,
        source=source,
        source_priority=prio,
        member_id=member.id if member else None,
        chamber=chamber,
        transaction_date=transaction_date,
        disclosure_date=disclosure_date,
        owner=owner,
        ticker=ticker,
        asset_name=asset_name,
        asset_type=asset_type,
        transaction_type=transaction_type,
        amount_min=amount_min,
        amount_max=amount_max,
        amount_range_raw=amount_range_raw,
        cap_gains_over_200=cap_gains_over_200,
        comment=comment,
        dedup_key=key,
        created_at=dt.datetime.now(dt.timezone.utc),
    )
    update_cols = {c: getattr(stmt.excluded, c) for c in _TRADE_COLS}
    stmt = stmt.on_conflict_do_update(
        index_elements=["dedup_key"],
        set_=update_cols,
        # only let an equal-or-higher-priority source overwrite an existing row
        where=Trade.source_priority <= stmt.excluded.source_priority,
    )
    session.execute(stmt)
