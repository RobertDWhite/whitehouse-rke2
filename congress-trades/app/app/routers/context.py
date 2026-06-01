import datetime as dt
import os

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import LegislativeEvent, Member, TickerMeta, Trade, TradeReconciliation
from ..enrich import enrich_rows

router = APIRouter()
READONLY = os.environ.get("PUBLIC_READONLY", "").lower() in ("1", "true", "yes")

_MID = (func.coalesce(Trade.amount_min, 0) + func.coalesce(Trade.amount_max, Trade.amount_min, 0)) / 2.0


def _guard():
    if READONLY:
        raise HTTPException(403, "read-only mode")


class ReconciliationResolution(BaseModel):
    status: str
    note: str | None = None


@router.get("/committees")
def committees(db: Session = Depends(get_db), limit: int = Query(100, le=300)):
    members = db.scalars(select(Member).where(Member.committees.isnot(None))).all()
    out = {}
    for m in members:
        for c in m.committees or []:
            item = out.setdefault(c, {"committee": c, "members": 0, "sectors": set(), "trades": 0, "volume": 0.0})
            item["members"] += 1
            item["sectors"].update(m.committee_sectors or [])
    trade_rows = db.execute(
        select(Member.id, func.count(Trade.id), func.coalesce(func.sum(_MID), 0))
        .join(Trade, Trade.member_id == Member.id)
        .where(Member.committees.isnot(None))
        .group_by(Member.id)
    ).all()
    by_member = {mid: (int(c or 0), float(v or 0)) for mid, c, v in trade_rows}
    for m in members:
        ccount, vol = by_member.get(m.id, (0, 0.0))
        for c in m.committees or []:
            out[c]["trades"] += ccount
            out[c]["volume"] += vol
    rows = sorted(out.values(), key=lambda x: x["volume"], reverse=True)[:limit]
    for row in rows:
        row["sectors"] = sorted(row["sectors"])
    return {"items": rows}


@router.get("/committees/{name}")
def committee_detail(name: str, db: Session = Depends(get_db)):
    members = db.scalars(select(Member).where(Member.committees.isnot(None))).all()
    matched = [m for m in members if any(name.lower() in c.lower() for c in (m.committees or []))]
    ids = [m.id for m in matched]
    rows = []
    if ids:
        rows = db.execute(
            select(Trade.ticker, func.max(TickerMeta.company), func.max(TickerMeta.sector), func.count(), func.coalesce(func.sum(_MID), 0))
            .join(TickerMeta, TickerMeta.ticker == Trade.ticker, isouter=True)
            .where(and_(Trade.member_id.in_(ids), Trade.ticker.isnot(None)))
            .group_by(Trade.ticker)
            .order_by(func.sum(_MID).desc())
            .limit(50)
        ).all()
    return {
        "committee": name,
        "members": [{"id": m.id, "full_name": m.full_name, "party": m.party, "state": m.state, "district": m.district} for m in matched],
        "tickers": [
            {"ticker": tk, "company": co, "sector": sec, "count": int(c or 0), "volume": float(v or 0)}
            for tk, co, sec, c, v in rows
        ],
    }


@router.get("/legislative-events")
def legislative_events(
    db: Session = Depends(get_db),
    member_id: int | None = None,
    ticker: str | None = None,
    sector: str | None = None,
    event_type: str | None = None,
    days: int = Query(120, le=730),
    limit: int = Query(100, le=500),
):
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    stmt = select(LegislativeEvent, Member).join(Member, Member.id == LegislativeEvent.member_id, isouter=True).where(
        LegislativeEvent.occurred_at >= since
    )
    if member_id:
        stmt = stmt.where(LegislativeEvent.member_id == member_id)
    if ticker:
        meta = db.get(TickerMeta, ticker.upper())
        if meta and meta.sector:
            stmt = stmt.where(LegislativeEvent.sector == meta.sector)
        else:
            stmt = stmt.where(LegislativeEvent.ticker == ticker.upper())
    if sector:
        stmt = stmt.where(LegislativeEvent.sector == sector)
    if event_type:
        stmt = stmt.where(LegislativeEvent.event_type == event_type)
    rows = db.execute(stmt.order_by(LegislativeEvent.occurred_at.desc().nullslast()).limit(limit)).all()
    return {
        "items": [
            {
                "id": e.id,
                "event_type": e.event_type,
                "title": e.title,
                "url": e.url,
                "occurred_at": e.occurred_at.isoformat() if e.occurred_at else None,
                "member_id": e.member_id,
                "member": m.full_name if m else None,
                "party": m.party if m else None,
                "sector": e.sector,
                "committee": e.committee,
            }
            for e, m in rows
        ]
    }


@router.get("/reconciliation")
def reconciliation(db: Session = Depends(get_db), status: str = "open", limit: int = Query(100, le=500)):
    by_kind = dict(db.execute(select(TradeReconciliation.kind, func.count()).where(TradeReconciliation.status == "open").group_by(TradeReconciliation.kind)).all())
    by_status = dict(db.execute(select(TradeReconciliation.status, func.count()).group_by(TradeReconciliation.status)).all())
    status_filter = TradeReconciliation.status == status if status != "all" else True
    rows = db.scalars(
        select(TradeReconciliation)
        .where(status_filter)
        .order_by(TradeReconciliation.severity.desc(), TradeReconciliation.created_at.desc())
        .limit(limit)
    ).all()
    return {
        "by_kind": by_kind,
        "by_status": by_status,
        "items": [
            {
                "id": r.id,
                "kind": r.kind,
                "severity": r.severity,
                "confidence": float(r.confidence) if r.confidence is not None else None,
                "status": r.status,
                "comparison_source": r.comparison_source,
                "primary_trade_id": r.primary_trade_id,
                "comparison_trade_id": r.comparison_trade_id,
                "detail": r.detail or {},
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    }


@router.post("/reconciliation/{issue_id}/resolve")
def resolve_reconciliation(issue_id: int, body: ReconciliationResolution, db: Session = Depends(get_db)):
    _guard()
    if body.status not in ("resolved", "ignored", "open"):
        raise HTTPException(400, "status must be resolved, ignored, or open")
    issue = db.get(TradeReconciliation, issue_id)
    if not issue:
        raise HTTPException(404, "reconciliation issue not found")
    issue.status = body.status
    issue.resolution_note = body.note
    issue.resolved_at = dt.datetime.now(dt.timezone.utc) if body.status in ("resolved", "ignored") else None
    db.commit()
    return {"status": issue.status}


@router.get("/analytics/disclosure-lag")
def disclosure_lag(db: Session = Depends(get_db), days: int = Query(365, le=3650), limit: int = Query(25, le=100)):
    since = dt.date.today() - dt.timedelta(days=days)
    lag = Trade.disclosure_date - Trade.transaction_date
    base = and_(Trade.transaction_date.isnot(None), Trade.disclosure_date.isnot(None), Trade.disclosure_date >= since)
    total = db.scalar(select(func.count(Trade.id)).where(base)) or 0
    avg_lag = db.scalar(select(func.avg(lag)).where(base))
    late_flag = case((lag >= 45, 1.0), else_=0.0)
    pct_late = db.scalar(select(func.avg(late_flag)).where(base)) if total else None

    buckets = [
        ("0-7d", 0, 7),
        ("8-14d", 8, 14),
        ("15-30d", 15, 30),
        ("31-44d", 31, 44),
        ("45-60d", 45, 60),
        ("61d+", 61, 100000),
    ]
    hist = [
        {"bucket": label, "count": db.scalar(select(func.count(Trade.id)).where(and_(base, lag >= lo, lag <= hi))) or 0}
        for label, lo, hi in buckets
    ]

    by_chamber = [
        {"chamber": ch or "unknown", "avg_lag_days": float(avg or 0), "late_rate": float(late or 0), "count": int(c or 0)}
        for ch, avg, late, c in db.execute(
            select(Trade.chamber, func.avg(lag), func.avg(late_flag), func.count())
            .where(base)
            .group_by(Trade.chamber)
            .order_by(func.avg(lag).desc())
        ).all()
    ]

    by_party = [
        {"party": p or "unknown", "avg_lag_days": float(avg or 0), "late_rate": float(late or 0), "count": int(c or 0)}
        for p, avg, late, c in db.execute(
            select(Member.party, func.avg(lag), func.avg(late_flag), func.count())
            .join(Member, Member.id == Trade.member_id, isouter=True)
            .where(base)
            .group_by(Member.party)
            .order_by(func.avg(lag).desc())
        ).all()
    ]

    worst_members = [
        {
            "member_id": mid,
            "member": name,
            "party": party,
            "state": state,
            "avg_lag_days": float(avg or 0),
            "late_rate": float(late or 0),
            "count": int(c or 0),
        }
        for mid, name, party, state, avg, late, c in db.execute(
            select(Member.id, Member.full_name, Member.party, Member.state, func.avg(lag), func.avg(late_flag), func.count())
            .join(Member, Member.id == Trade.member_id)
            .where(base)
            .group_by(Member.id)
            .having(func.count() >= 3)
            .order_by(func.avg(lag).desc())
            .limit(limit)
        ).all()
    ]

    late_rows = db.execute(
        select(Trade, Member)
        .join(Member, Member.id == Trade.member_id, isouter=True)
        .where(and_(base, lag >= 45))
        .order_by(lag.desc(), Trade.disclosure_date.desc())
        .limit(limit)
    ).all()

    return {
        "window_days": days,
        "total": total,
        "avg_lag_days": float(avg_lag) if avg_lag is not None else None,
        "late_rate": float(pct_late) if pct_late is not None else None,
        "histogram": hist,
        "by_chamber": by_chamber,
        "by_party": by_party,
        "worst_members": worst_members,
        "late_trades": enrich_rows(db, late_rows),
    }
