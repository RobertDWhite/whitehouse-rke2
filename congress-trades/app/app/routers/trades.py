from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Member, Trade
from ..serialize import trade_dict

router = APIRouter()


@router.get("/trades")
def list_trades(
    db: Session = Depends(get_db),
    chamber: str | None = None,
    party: str | None = None,
    state: str | None = None,
    member_id: int | None = None,
    ticker: str | None = None,
    transaction_type: str | None = None,
    source: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    min_amount: int | None = None,
    q: str | None = None,
    sort: str = "transaction_date",
    order: str = "desc",
    limit: int = Query(100, le=500),
    offset: int = 0,
):
    stmt = select(Trade, Member).join(Member, Trade.member_id == Member.id, isouter=True)
    conds = []
    if chamber:
        conds.append(Trade.chamber == chamber)
    if party:
        conds.append(Member.party == party)
    if state:
        conds.append(Member.state == state)
    if member_id:
        conds.append(Trade.member_id == member_id)
    if ticker:
        conds.append(Trade.ticker == ticker.upper())
    if transaction_type:
        conds.append(Trade.transaction_type == transaction_type)
    if source:
        conds.append(Trade.source == source)
    if start_date:
        conds.append(Trade.transaction_date >= start_date)
    if end_date:
        conds.append(Trade.transaction_date <= end_date)
    if min_amount:
        conds.append(Trade.amount_max >= min_amount)
    if q:
        like = f"%{q}%"
        conds.append(
            or_(
                Trade.asset_name.ilike(like),
                Trade.ticker.ilike(like),
                Member.full_name.ilike(like),
            )
        )
    if conds:
        stmt = stmt.where(and_(*conds))

    total = db.scalar(select(func.count()).select_from(stmt.subquery()))

    sort_map = {
        "transaction_date": Trade.transaction_date,
        "disclosure_date": Trade.disclosure_date,
        "amount": Trade.amount_max,
        "ticker": Trade.ticker,
    }
    col = sort_map.get(sort, Trade.transaction_date)
    col = col.desc().nullslast() if order == "desc" else col.asc().nullslast()

    rows = db.execute(
        stmt.order_by(col, Trade.id.desc()).limit(limit).offset(offset)
    ).all()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [trade_dict(t, m) for t, m in rows],
    }
