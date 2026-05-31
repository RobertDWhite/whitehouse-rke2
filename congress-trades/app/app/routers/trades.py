from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..enrich import enrich_rows
from ..models import Filing, Member, Trade, TradeReconciliation, TradeSignal

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
    min_lag: int | None = None,
    max_lag: int | None = None,
    signal: str | None = None,
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
        # use the upper bound, falling back to the lower bound for open-ended "over $X"
        conds.append(func.coalesce(Trade.amount_max, Trade.amount_min) >= min_amount)
    if min_lag is not None:
        conds.append((Trade.disclosure_date - Trade.transaction_date) >= min_lag)
    if max_lag is not None:
        conds.append((Trade.disclosure_date - Trade.transaction_date) <= max_lag)
    if signal:
        conds.append(
            Trade.id.in_(select(TradeSignal.trade_id).where(TradeSignal.signal_type == signal))
        )
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
        "items": enrich_rows(db, rows),
    }


@router.get("/trades/{trade_id}")
def get_trade(trade_id: int, db: Session = Depends(get_db)):
    row = db.execute(
        select(Trade, Member, Filing)
        .join(Member, Trade.member_id == Member.id, isouter=True)
        .join(Filing, Trade.filing_id == Filing.id, isouter=True)
        .where(Trade.id == trade_id)
    ).one_or_none()
    if not row:
        raise HTTPException(404, "trade not found")
    t, m, f = row
    item = enrich_rows(db, [(t, m)])[0]
    item["filing"] = {
        "id": f.id,
        "source": f.source,
        "doc_id": f.doc_id,
        "filing_type": f.filing_type,
        "filing_date": f.filing_date.isoformat() if f.filing_date else None,
        "parse_status": f.parse_status,
        "source_url": f.source_url,
        "fetched_at": f.fetched_at.isoformat() if f.fetched_at else None,
        "raw_excerpt": (f.raw_text or "")[:1200],
    } if f else None
    item["provenance"] = {
        "dedup_key": t.dedup_key,
        "source_priority": t.source_priority,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "primary_source": t.source in ("house_primary", "senate_primary"),
        "amount_parse": {
            "raw": t.amount_range_raw,
            "min": float(t.amount_min) if t.amount_min is not None else None,
            "max": float(t.amount_max) if t.amount_max is not None else None,
        },
    }
    item["reconciliation"] = [
        {
            "kind": r.kind,
            "severity": r.severity,
            "comparison_source": r.comparison_source,
            "comparison_trade_id": r.comparison_trade_id,
            "detail": r.detail or {},
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in db.scalars(
            select(TradeReconciliation)
            .where(or_(TradeReconciliation.primary_trade_id == trade_id, TradeReconciliation.comparison_trade_id == trade_id))
            .order_by(TradeReconciliation.severity.desc())
        ).all()
    ]
    return item
