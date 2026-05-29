from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Filing, Trade

router = APIRouter()


@router.get("/filings")
def filings_overview(db: Session = Depends(get_db)):
    """Provenance / parse-health: proves the self-parsed primary pipeline works
    independently of the third-party Lambda feed."""
    by_source = dict(
        db.execute(select(Filing.source, func.count()).group_by(Filing.source)).all()
    )
    by_status = dict(
        db.execute(
            select(Filing.parse_status, func.count()).group_by(Filing.parse_status)
        ).all()
    )
    trades_by_source = dict(
        db.execute(select(Trade.source, func.count()).group_by(Trade.source)).all()
    )
    last_fetch = db.scalar(select(func.max(Filing.fetched_at)))

    last_by_source = {
        src: ts.isoformat() if ts else None
        for src, ts in db.execute(
            select(Filing.source, func.max(Filing.fetched_at)).group_by(Filing.source)
        ).all()
    }

    return {
        "filings_by_source": by_source,
        "filings_by_parse_status": by_status,
        "trades_by_source": trades_by_source,
        "last_fetch": last_fetch.isoformat() if last_fetch else None,
        "last_fetch_by_source": last_by_source,
    }
