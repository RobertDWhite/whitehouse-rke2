from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import StrategyRun, TickerMeta

router = APIRouter()

DISCLAIMER = (
    "Backtest of publicly-disclosed congressional buys. Each position enters at the first close "
    "ON OR AFTER the public disclosure date (which lags the actual trade up to 45 days), price-return "
    "only (no dividends/fees/slippage), $1 per disclosed buy held to today, benchmarked vs SPY. "
    "Hypothetical, in-sample, single market regime. Not advice; past performance is not predictive."
)


def _row(s, full=False):
    d = {
        "strategy_key": s.strategy_key,
        "label": s.label,
        "total_return": float(s.total_return) if s.total_return is not None else None,
        "cagr": float(s.cagr) if s.cagr is not None else None,
        "max_drawdown": float(s.max_drawdown) if s.max_drawdown is not None else None,
        "excess_vs_spy": float(s.excess_vs_spy) if s.excess_vs_spy is not None else None,
        "n_positions": s.n_positions,
        "generated_at": s.generated_at.isoformat() if s.generated_at else None,
    }
    if full:
        d["equity_curve"] = s.equity_curve or []
        d["holdings"] = s.holdings or []
    return d


@router.get("/strategies")
def list_strategies(db: Session = Depends(get_db)):
    rows = db.scalars(select(StrategyRun).order_by(StrategyRun.excess_vs_spy.desc().nullslast())).all()
    return {"disclaimer": DISCLAIMER, "items": [_row(s) for s in rows]}


@router.get("/strategies/{key}")
def get_strategy(key: str, db: Session = Depends(get_db)):
    s = db.get(StrategyRun, key)
    if not s:
        raise HTTPException(404, "unknown strategy")
    out = {"disclaimer": DISCLAIMER, **_row(s, full=True)}
    # decorate holdings with sector/company
    tks = [h["ticker"] for h in (s.holdings or [])]
    meta = {m.ticker: m for m in db.scalars(select(TickerMeta).where(TickerMeta.ticker.in_(tks))).all()} if tks else {}
    for h in out["holdings"]:
        m = meta.get(h["ticker"])
        h["sector"] = m.sector if m else None
        h["company"] = m.company if m else None
    return out
