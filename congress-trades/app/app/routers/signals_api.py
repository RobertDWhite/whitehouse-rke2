from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..enrich import enrich_rows
from ..models import Member, Trade, TradeSignal

router = APIRouter()


@router.get("/signals")
def recent_signals(
    db: Session = Depends(get_db),
    signal_type: str | None = None,
    limit: int = Query(100, le=300),
):
    """Recent trades carrying signals, highest summed score first."""
    score_sum = func.sum(TradeSignal.score).label("score")
    inner = select(TradeSignal.trade_id, score_sum).group_by(TradeSignal.trade_id)
    if signal_type:
        inner = inner.where(TradeSignal.signal_type == signal_type)
    inner = inner.subquery()

    rows = db.execute(
        select(Trade, Member)
        .join(inner, inner.c.trade_id == Trade.id)
        .join(Member, Member.id == Trade.member_id, isouter=True)
        .order_by(inner.c.score.desc(), Trade.disclosure_date.desc().nullslast())
        .limit(limit)
    ).all()

    by_type = dict(
        db.execute(select(TradeSignal.signal_type, func.count()).group_by(TradeSignal.signal_type)).all()
    )
    return {"by_type": by_type, "items": enrich_rows(db, rows)}
