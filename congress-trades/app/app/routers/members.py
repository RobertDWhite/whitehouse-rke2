from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Member, Trade
from ..serialize import member_dict, trade_dict

router = APIRouter()


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
    stmt = (
        select(Member, count_col)
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
    return {"items": [member_dict(m, tc) for m, tc in rows]}


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

    return {
        "member": member_dict(m, len(trades)),
        "by_transaction_type": by_type,
        "top_tickers": top_tickers,
        "trades": [trade_dict(t, m) for t in trades],
    }
