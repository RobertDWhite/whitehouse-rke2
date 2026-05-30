"""Personal paper-portfolio + congressional-overlap. Single user behind SSO (mutations are
disabled in public read-only mode via PUBLIC_READONLY)."""
import datetime as dt
import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import and_, case, delete, func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Holding, Member, TickerMeta, Trade, TradeSignal

router = APIRouter()
READONLY = os.environ.get("PUBLIC_READONLY", "").lower() in ("1", "true", "yes")
_MID = (func.coalesce(Trade.amount_min, 0) + func.coalesce(Trade.amount_max, Trade.amount_min, 0)) / 2.0


class HoldingIn(BaseModel):
    ticker: str
    shares: float | None = None
    cost_basis: float | None = None
    note: str | None = None


def _guard():
    if READONLY:
        raise HTTPException(403, "read-only mode")


@router.get("/portfolio/holdings")
def holdings(db: Session = Depends(get_db)):
    return {"items": [
        {"id": h.id, "ticker": h.ticker, "shares": float(h.shares) if h.shares is not None else None,
         "cost_basis": float(h.cost_basis) if h.cost_basis is not None else None, "note": h.note}
        for h in db.scalars(select(Holding).order_by(Holding.ticker)).all()
    ], "readonly": READONLY}


@router.post("/portfolio/holdings")
def add_holding(h: HoldingIn, db: Session = Depends(get_db)):
    _guard()
    tk = h.ticker.upper()
    existing = db.scalar(select(Holding).where(Holding.ticker == tk))
    if existing:
        existing.shares, existing.cost_basis, existing.note = h.shares, h.cost_basis, h.note
    else:
        db.add(Holding(ticker=tk, shares=h.shares, cost_basis=h.cost_basis, note=h.note))
    db.commit()
    return {"status": "ok"}


@router.delete("/portfolio/holdings/{hid}")
def remove_holding(hid: int, db: Session = Depends(get_db)):
    _guard()
    db.execute(delete(Holding).where(Holding.id == hid))
    db.commit()
    return {"status": "removed"}


@router.get("/portfolio/overlap")
def overlap(db: Session = Depends(get_db), days: int = 90):
    """For each held ticker, recent congressional net buy/sell pressure + buyers."""
    since = dt.date.today() - dt.timedelta(days=days)
    tickers = [h.ticker for h in db.scalars(select(Holding)).all()]
    if not tickers:
        return {"items": []}
    net = func.sum(case((Trade.transaction_type == "purchase", _MID), (Trade.transaction_type == "sale", -_MID), else_=0))
    rows = db.execute(
        select(
            Trade.ticker, net,
            func.count(func.distinct(case((Trade.transaction_type == "purchase", Trade.member_id)))),
            func.count(func.distinct(case((Trade.transaction_type == "sale", Trade.member_id)))),
            func.max(Trade.disclosure_date),
        )
        .where(and_(Trade.ticker.in_(tickers), Trade.disclosure_date >= since))
        .group_by(Trade.ticker)
    ).all()
    by_ticker = {tk: {"net_notional": float(nv or 0), "buyers": int(b or 0), "sellers": int(s or 0),
                      "last_seen": ls.isoformat() if ls else None} for tk, nv, b, s, ls in rows}
    return {"items": [{"ticker": tk, **by_ticker.get(tk, {"net_notional": 0, "buyers": 0, "sellers": 0, "last_seen": None})} for tk in tickers]}
