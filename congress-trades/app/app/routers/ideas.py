"""Ranked "ideas" surfaces — all are views of ALREADY-DISCLOSED, lagged (<=45 days) data.
Informational only, not advice. Every payload carries the disclaimer + staleness dates."""
import datetime as dt

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Numeric, and_, case, cast, func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..enrich import enrich_rows
from ..models import Member, TickerMeta, Trade, TradeSignal

router = APIRouter()

DISCLAIMER = (
    "Informational only — not financial advice. All data is publicly disclosed under the STOCK "
    "Act with a delay of up to 45 days, so it is NOT real-time and NOT a signal to trade now. "
    "Disclosed trades are legal. Returns are a hypothetical follower's, measured from the public "
    "disclosure date, benchmarked vs SPY. Past performance does not predict future results."
)

_MID = (func.coalesce(Trade.amount_min, 0) + func.coalesce(Trade.amount_max, Trade.amount_min, 0)) / 2.0


def _recent_trades_with_signal(db, signal_type, days, limit, extra=None):
    since = dt.date.today() - dt.timedelta(days=days)
    conds = [TradeSignal.signal_type == signal_type, Trade.disclosure_date >= since]
    if extra is not None:
        conds.append(extra)
    rows = db.execute(
        select(Trade, Member)
        .join(TradeSignal, TradeSignal.trade_id == Trade.id)
        .join(Member, Member.id == Trade.member_id, isouter=True)
        .where(and_(*conds))
        .order_by(TradeSignal.score.desc(), Trade.disclosure_date.desc().nullslast())
        .limit(limit)
    ).all()
    return enrich_rows(db, rows)


@router.get("/ideas")
def ideas(
    db: Session = Depends(get_db),
    window: int = Query(90, le=365),
    party: str | None = None,
    chamber: str | None = None,
):
    since = dt.date.today() - dt.timedelta(days=window)

    # ---- Accumulation watchlist: net buy notional per ticker for the cohort ----
    net = func.sum(
        case((Trade.transaction_type == "purchase", _MID), (Trade.transaction_type == "sale", -_MID), else_=0)
    ).label("net")
    buyers = func.count(func.distinct(case((Trade.transaction_type == "purchase", Trade.member_id)))).label("buyers")
    acc_q = (
        select(
            Trade.ticker, net, buyers,
            func.max(Trade.disclosure_date).label("last_seen"),
            func.max(TickerMeta.sector).label("sector"),
            func.max(TickerMeta.company).label("company"),
        )
        .join(Member, Member.id == Trade.member_id, isouter=True)
        .join(TickerMeta, TickerMeta.ticker == Trade.ticker, isouter=True)
        .where(and_(Trade.disclosure_date >= since, Trade.ticker.isnot(None)))
    )
    if party:
        acc_q = acc_q.where(Member.party == party)
    if chamber:
        acc_q = acc_q.where(Trade.chamber == chamber)
    acc_q = acc_q.group_by(Trade.ticker).having(net > 0).order_by(net.desc()).limit(25)

    # per-ticker max conviction (a watchlist-grade signal strength)
    conv_by_ticker = dict(
        db.execute(
            select(Trade.ticker, func.max(TradeSignal.score))
            .join(TradeSignal, and_(TradeSignal.trade_id == Trade.id, TradeSignal.signal_type == "conviction"))
            .where(and_(Trade.disclosure_date >= since, Trade.ticker.isnot(None)))
            .group_by(Trade.ticker)
        ).all()
    )
    accumulation = [
        {
            "ticker": tk, "company": company, "sector": sector,
            "net_notional": float(netv or 0), "buyers": int(bc or 0),
            "last_seen": ls.isoformat() if ls else None,
            "conviction": conv_by_ticker.get(tk),
        }
        for tk, netv, bc, ls, sector, company in db.execute(acc_q).all()
    ]

    return {
        "disclaimer": DISCLAIMER,
        "window_days": window,
        "accumulation": accumulation,
        "high_conviction": _recent_trades_with_signal(db, "conviction", 30, 15, extra=(Trade.transaction_type == "purchase")),
        "cluster_buys": _recent_trades_with_signal(db, "cluster_buy", 21, 15),
        "cluster_dumps": _recent_trades_with_signal(db, "cluster_sell", 21, 15),
        "unusual_options": _recent_trades_with_signal(db, "options", 30, 12),
        "conflicts": _recent_trades_with_signal(db, "conflict", 60, 12),
    }
