import datetime as dt

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..enrich import enrich_rows
from ..models import Member, TickerMeta, Trade

router = APIRouter()

_MID = (func.coalesce(Trade.amount_min, 0) + func.coalesce(Trade.amount_max, Trade.amount_min, 0)) / 2.0


def _count_between(db, start, end):
    return db.scalar(
        select(func.count(Trade.id)).where(and_(Trade.disclosure_date >= start, Trade.disclosure_date < end))
    ) or 0


def _volume_between(db, start, end):
    return float(
        db.scalar(select(func.coalesce(func.sum(_MID), 0)).where(and_(Trade.disclosure_date >= start, Trade.disclosure_date < end)))
        or 0
    )


@router.get("/stats")
def stats(db: Session = Depends(get_db)):
    total = db.scalar(select(func.count(Trade.id))) or 0

    by_chamber = dict(
        db.execute(select(Trade.chamber, func.count()).group_by(Trade.chamber)).all()
    )
    by_type = dict(
        db.execute(
            select(Trade.transaction_type, func.count()).group_by(Trade.transaction_type)
        ).all()
    )
    by_source = dict(
        db.execute(select(Trade.source, func.count()).group_by(Trade.source)).all()
    )

    top_traders = [
        {
            "member_id": mid,
            "member": name,
            "party": party,
            "state": state,
            "district": district,
            "chamber": chamber,
            "count": c,
        }
        for mid, name, party, state, district, chamber, c in db.execute(
            select(
                Member.id,
                Member.full_name,
                Member.party,
                Member.state,
                Member.district,
                Member.chamber,
                func.count(Trade.id),
            )
            .join(Trade, Trade.member_id == Member.id)
            .group_by(Member.id)
            .order_by(func.count(Trade.id).desc())
            .limit(15)
        ).all()
    ]

    top_tickers = [
        {"ticker": tk, "count": c}
        for tk, c in db.execute(
            select(Trade.ticker, func.count())
            .where(Trade.ticker.isnot(None))
            .group_by(Trade.ticker)
            .order_by(func.count().desc())
            .limit(15)
        ).all()
    ]

    recent = db.execute(
        select(Trade, Member)
        .join(Member, Trade.member_id == Member.id, isouter=True)
        .order_by(Trade.disclosure_date.desc().nullslast(), Trade.id.desc())
        .limit(20)
    ).all()

    # KPI deltas (by disclosure date)
    today = dt.date.today()
    d7, d14, d30, d60 = today - dt.timedelta(days=7), today - dt.timedelta(days=14), today - dt.timedelta(days=30), today - dt.timedelta(days=60)
    kpi = {
        "count_7d": _count_between(db, d7, today),
        "count_prior_7d": _count_between(db, d14, d7),
        "volume_30d": _volume_between(db, d30, today),
        "volume_prior_30d": _volume_between(db, d60, d30),
    }

    # hot tickers last 7d with buy/sell split
    hot = [
        {"ticker": tk, "count": int(c or 0), "buys": int(b or 0), "sells": int(s or 0)}
        for tk, c, b, s in db.execute(
            select(
                Trade.ticker, func.count(),
                func.sum(case((Trade.transaction_type == "purchase", 1), else_=0)),
                func.sum(case((Trade.transaction_type == "sale", 1), else_=0)),
            )
            .where(and_(Trade.ticker.isnot(None), Trade.disclosure_date >= d7))
            .group_by(Trade.ticker)
            .order_by(func.count().desc())
            .limit(12)
        ).all()
    ]

    return {
        "total_trades": total,
        "by_chamber": by_chamber,
        "by_transaction_type": by_type,
        "by_source": by_source,
        "kpi": kpi,
        "hot_tickers_7d": hot,
        "top_traders": top_traders,
        "top_tickers": top_tickers,
        "recent": enrich_rows(db, recent),
    }


@router.get("/stats/timeseries")
def timeseries(db: Session = Depends(get_db), days: int = Query(90, le=365)):
    """Weekly buy/sell/exchange counts by disclosure week."""
    since = dt.date.today() - dt.timedelta(days=days)
    wk = func.date_trunc("week", Trade.disclosure_date).label("wk")
    rows = db.execute(
        select(
            wk,
            func.sum(case((Trade.transaction_type == "purchase", 1), else_=0)),
            func.sum(case((Trade.transaction_type == "sale", 1), else_=0)),
            func.sum(case((Trade.transaction_type == "exchange", 1), else_=0)),
        )
        .where(Trade.disclosure_date >= since)
        .group_by(wk)
        .order_by(wk)
    ).all()
    return {
        "items": [
            {"week": w.date().isoformat() if w else None, "purchase": int(p or 0), "sale": int(s or 0), "exchange": int(e or 0)}
            for w, p, s, e in rows
        ]
    }


@router.get("/stats/sectors")
def sectors(db: Session = Depends(get_db), days: int = Query(90, le=365)):
    """Disclosed $ volume by sector (for a treemap)."""
    since = dt.date.today() - dt.timedelta(days=days)
    rows = db.execute(
        select(func.coalesce(TickerMeta.sector, "Unknown"), func.sum(_MID), func.count())
        .join(Trade, Trade.ticker == TickerMeta.ticker, isouter=False)
        .where(Trade.disclosure_date >= since)
        .group_by(TickerMeta.sector)
        .order_by(func.sum(_MID).desc())
    ).all()
    return {"items": [{"sector": sec, "volume": float(v or 0), "count": int(c or 0)} for sec, v, c in rows]}
