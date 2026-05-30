"""Member leaderboards. The performance board uses follower return since the PUBLIC disclosure
date (lagged, conservative) benchmarked vs SPY — informational, not a claim of skill."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import Numeric, and_, case, cast, func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Member, Trade
from ..serialize import member_dict

router = APIRouter()

_MID = (func.coalesce(Trade.amount_min, 0) + func.coalesce(Trade.amount_max, Trade.amount_min, 0)) / 2.0
_EXCESS = Trade.return_pct - Trade.bench_return_pct


@router.get("/leaderboard")
def leaderboard(
    db: Session = Depends(get_db),
    metric: str = Query("performance"),  # performance | volume | activity | late
    min_trades: int = Query(5, ge=1),
    limit: int = Query(50, le=200),
):
    if metric == "performance":
        wt_excess = (func.sum(_MID * _EXCESS) / func.nullif(func.sum(_MID), 0)).label("wt_excess")
        hit = func.avg(case((_EXCESS > 0, 1.0), else_=0.0)).label("hit_rate")
        n = func.count().label("n")
        rows = db.execute(
            select(
                Member, wt_excess, hit, n,
                func.avg(Trade.return_pct).label("avg_return"),
                func.avg(Trade.bench_return_pct).label("avg_bench"),
            )
            .join(Trade, Trade.member_id == Member.id)
            .where(and_(Trade.transaction_type == "purchase", Trade.return_pct.isnot(None), Trade.bench_return_pct.isnot(None)))
            .group_by(Member.id)
            .having(func.count() >= min_trades)
            .order_by(wt_excess.desc().nullslast())
            .limit(limit)
        ).all()
        return {
            "metric": "performance",
            "note": "Follower return since public disclosure (lagged up to 45 days) minus SPY over the same window. "
                    f"Min {min_trades} priced trades. Price return only; past performance is not predictive.",
            "items": [
                {
                    **member_dict(m),
                    "wt_excess_pct": float(we or 0),
                    "hit_rate": float(hr or 0),
                    "n": int(nn or 0),
                    "avg_return_pct": float(ar or 0),
                    "avg_bench_pct": float(ab or 0),
                }
                for m, we, hr, nn, ar, ab in rows
            ],
        }

    # count/volume/late boards
    count_col = func.count(Trade.id)
    vol_col = func.coalesce(func.sum(_MID), 0)
    lag_col = func.avg(Trade.disclosure_date - Trade.transaction_date)
    order = {"volume": vol_col, "activity": count_col, "late": lag_col}.get(metric, count_col)
    rows = db.execute(
        select(Member, count_col, vol_col, lag_col)
        .join(Trade, Trade.member_id == Member.id)
        .group_by(Member.id)
        .having(count_col >= min_trades)
        .order_by(order.desc())
        .limit(limit)
    ).all()
    return {
        "metric": metric,
        "items": [
            {**member_dict(m, c), "est_volume": float(v or 0), "avg_lag_days": float(lag) if lag is not None else None}
            for m, c, v, lag in rows
        ],
    }
