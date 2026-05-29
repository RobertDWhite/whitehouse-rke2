from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Member, Trade
from ..serialize import trade_dict

router = APIRouter()


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

    return {
        "total_trades": total,
        "by_chamber": by_chamber,
        "by_transaction_type": by_type,
        "by_source": by_source,
        "top_traders": top_traders,
        "top_tickers": top_tickers,
        "recent": [trade_dict(t, m) for t, m in recent],
    }
