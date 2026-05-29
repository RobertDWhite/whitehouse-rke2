from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import AiSummary

router = APIRouter()

DISCLAIMER = (
    "AI-generated summary of publicly disclosed congressional trades (STOCK Act). "
    "Not financial advice. Disclosed trades are legal. Amounts are reported ranges. "
    "May contain errors — verify against the source filings."
)


def _serialize(s):
    if not s:
        return None
    return {
        "scope": s.scope,
        "member_id": s.member_id,
        "window_days": s.window_days,
        "summary_md": s.summary_md,
        "observations": s.observations or [],
        "model": s.model,
        "trade_count": s.trade_count,
        "generated_at": s.generated_at.isoformat() if s.generated_at else None,
        "disclaimer": DISCLAIMER,
    }


@router.get("/ai/summary")
def ai_summary(db: Session = Depends(get_db), window: int = Query(7)):
    s = db.scalar(
        select(AiSummary)
        .where(and_(AiSummary.scope == "global", AiSummary.window_days == window))
        .order_by(AiSummary.generated_at.desc())
        .limit(1)
    )
    return _serialize(s) or {"summary_md": None}


@router.get("/ai/summary/member/{member_id}")
def ai_member_summary(member_id: int, db: Session = Depends(get_db), window: int = Query(30)):
    s = db.scalar(
        select(AiSummary)
        .where(and_(AiSummary.scope == "member", AiSummary.member_id == member_id, AiSummary.window_days == window))
        .order_by(AiSummary.generated_at.desc())
        .limit(1)
    )
    return _serialize(s) or {"summary_md": None}
