import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..enrich import enrich_rows
from ..models import Member, Trade, Watchlist

router = APIRouter()
READONLY = os.environ.get("PUBLIC_READONLY", "").lower() in ("1", "true", "yes")


def _guard():
    if READONLY:
        raise HTTPException(403, "read-only mode")


class WatchItem(BaseModel):
    kind: str  # member | ticker
    value: str
    min_score: int = 1


@router.get("/watchlist")
def list_watchlist(db: Session = Depends(get_db)):
    items = db.scalars(select(Watchlist)).all()
    # resolve member names for display
    member_ids = [int(w.value) for w in items if w.kind == "member" and w.value.isdigit()]
    names = dict(db.execute(select(Member.id, Member.full_name).where(Member.id.in_(member_ids))).all()) if member_ids else {}
    return {
        "items": [
            {"id": w.id, "kind": w.kind, "value": w.value,
             "label": names.get(int(w.value)) if (w.kind == "member" and w.value.isdigit()) else w.value}
            for w in items
        ]
    }


@router.post("/watchlist")
def add_watchlist(item: WatchItem, db: Session = Depends(get_db)):
    _guard()
    if item.kind not in ("member", "ticker"):
        raise HTTPException(400, "kind must be member or ticker")
    value = item.value.upper() if item.kind == "ticker" else item.value
    existing = db.scalar(select(Watchlist).where(Watchlist.kind == item.kind, Watchlist.value == value))
    if existing:
        return {"id": existing.id, "status": "exists"}
    w = Watchlist(kind=item.kind, value=value, min_score=item.min_score)
    db.add(w)
    db.commit()
    return {"id": w.id, "status": "added"}


@router.delete("/watchlist/{watch_id}")
def remove_watchlist(watch_id: int, db: Session = Depends(get_db)):
    _guard()
    db.execute(delete(Watchlist).where(Watchlist.id == watch_id))
    db.commit()
    return {"status": "removed"}


@router.get("/watchlist/feed")
def watchlist_feed(db: Session = Depends(get_db), limit: int = 100):
    """Recent trades for any watched member or ticker."""
    items = db.scalars(select(Watchlist)).all()
    member_ids = [int(w.value) for w in items if w.kind == "member" and w.value.isdigit()]
    tickers = [w.value.upper() for w in items if w.kind == "ticker"]
    if not member_ids and not tickers:
        return {"items": []}
    conds = []
    if member_ids:
        conds.append(Trade.member_id.in_(member_ids))
    if tickers:
        conds.append(Trade.ticker.in_(tickers))
    rows = db.execute(
        select(Trade, Member)
        .join(Member, Member.id == Trade.member_id, isouter=True)
        .where(or_(*conds))
        .order_by(Trade.disclosure_date.desc().nullslast(), Trade.id.desc())
        .limit(limit)
    ).all()
    return {"items": enrich_rows(db, rows)}
