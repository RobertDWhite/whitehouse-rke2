from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Member, Trade
from ..serialize import trade_dict

router = APIRouter()


@router.get("/tickers")
def top_tickers(db: Session = Depends(get_db), limit: int = Query(50, le=200)):
    rows = db.execute(
        select(Trade.ticker, func.count())
        .where(Trade.ticker.isnot(None))
        .group_by(Trade.ticker)
        .order_by(func.count().desc())
        .limit(limit)
    ).all()
    return {"items": [{"ticker": tk, "count": c} for tk, c in rows]}


@router.get("/tickers/{symbol}")
def ticker_detail(symbol: str, db: Session = Depends(get_db), limit: int = Query(500, le=1000)):
    sym = symbol.upper()
    rows = db.execute(
        select(Trade, Member)
        .join(Member, Trade.member_id == Member.id, isouter=True)
        .where(Trade.ticker == sym)
        .order_by(Trade.transaction_date.desc().nullslast(), Trade.id.desc())
        .limit(limit)
    ).all()
    by_type = dict(
        db.execute(
            select(Trade.transaction_type, func.count())
            .where(Trade.ticker == sym)
            .group_by(Trade.transaction_type)
        ).all()
    )
    return {
        "ticker": sym,
        "count": len(rows),
        "by_transaction_type": by_type,
        "items": [trade_dict(t, m) for t, m in rows],
    }
