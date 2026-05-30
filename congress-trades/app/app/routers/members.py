from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..enrich import enrich_rows
from ..models import Member, TickerMeta, Trade
from ..serialize import member_dict

router = APIRouter()

# Disclosed trading volume proxy: sum of per-trade amount-range midpoints.
# (Amounts are disclosed only as ranges; amount_max is null for open-ended "over $X".)
_MIDPOINT = (func.coalesce(Trade.amount_min, 0) + func.coalesce(Trade.amount_max, Trade.amount_min, 0)) / 2.0


@router.get("/members")
def list_members(
    db: Session = Depends(get_db),
    chamber: str | None = None,
    party: str | None = None,
    state: str | None = None,
    q: str | None = None,
    limit: int = Query(500, le=1000),
):
    count_col = func.count(Trade.id)
    vol_col = func.coalesce(func.sum(_MIDPOINT), 0)
    stmt = (
        select(Member, count_col, vol_col)
        .join(Trade, Trade.member_id == Member.id, isouter=True)
        .group_by(Member.id)
    )
    conds = []
    if chamber:
        conds.append(Member.chamber == chamber)
    if party:
        conds.append(Member.party == party)
    if state:
        conds.append(Member.state == state)
    if q:
        conds.append(Member.full_name.ilike(f"%{q}%"))
    if conds:
        stmt = stmt.where(and_(*conds))
    stmt = stmt.order_by(count_col.desc()).limit(limit)

    rows = db.execute(stmt).all()
    return {
        "items": [
            {**member_dict(m, tc), "est_volume": float(vol or 0)} for m, tc, vol in rows
        ]
    }


@router.get("/members/{member_id}")
def get_member(member_id: int, db: Session = Depends(get_db)):
    m = db.get(Member, member_id)
    if not m:
        raise HTTPException(status_code=404, detail="member not found")

    trades = db.scalars(
        select(Trade)
        .where(Trade.member_id == member_id)
        .order_by(Trade.transaction_date.desc().nullslast(), Trade.id.desc())
        .limit(1000)
    ).all()

    by_type = dict(
        db.execute(
            select(Trade.transaction_type, func.count())
            .where(Trade.member_id == member_id)
            .group_by(Trade.transaction_type)
        ).all()
    )
    top_tickers = [
        {"ticker": tk, "count": c}
        for tk, c in db.execute(
            select(Trade.ticker, func.count())
            .where(and_(Trade.member_id == member_id, Trade.ticker.isnot(None)))
            .group_by(Trade.ticker)
            .order_by(func.count().desc())
            .limit(15)
        ).all()
    ]

    est_volume = float(
        db.scalar(
            select(func.coalesce(func.sum(_MIDPOINT), 0)).where(Trade.member_id == member_id)
        )
        or 0
    )

    # monthly activity (for a sparkline)
    mwk = func.to_char(func.date_trunc("month", Trade.transaction_date), "YYYY-MM")
    monthly = [
        {"month": mo, "count": int(c or 0)}
        for mo, c in db.execute(
            select(mwk, func.count())
            .where(and_(Trade.member_id == member_id, Trade.transaction_date.isnot(None)))
            .group_by(mwk)
            .order_by(mwk)
        ).all()
    ]

    # disclosure timing + follower performance
    timing = db.execute(
        select(
            func.avg(Trade.disclosure_date - Trade.transaction_date),
            func.avg(case(((Trade.disclosure_date - Trade.transaction_date) >= 45, 1.0), else_=0.0)),
            func.avg(Trade.return_pct - Trade.bench_return_pct),
        ).where(and_(Trade.member_id == member_id, Trade.transaction_type == "purchase"))
    ).one()

    # sector mix (by volume) via ticker_meta
    sector_mix = [
        {"sector": sec or "Unknown", "volume": float(v or 0)}
        for sec, v in db.execute(
            select(func.coalesce(TickerMeta.sector, "Unknown"), func.sum(_MIDPOINT))
            .join(TickerMeta, TickerMeta.ticker == Trade.ticker)
            .where(Trade.member_id == member_id)
            .group_by(TickerMeta.sector)
            .order_by(func.sum(_MIDPOINT).desc())
            .limit(8)
        ).all()
    ]

    return {
        "member": {
            **member_dict(m, len(trades)),
            "est_volume": est_volume,
            "avg_lag_days": float(timing[0]) if timing[0] is not None else None,
            "pct_late": float(timing[1]) if timing[1] is not None else None,
            "wt_excess_pct": float(timing[2]) if timing[2] is not None else None,
        },
        "by_transaction_type": by_type,
        "top_tickers": top_tickers,
        "monthly_activity": monthly,
        "sector_mix": sector_mix,
        "trades": enrich_rows(db, [(t, m) for t in trades]),
    }
