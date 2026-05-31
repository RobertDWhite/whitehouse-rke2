from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..enrich import enrich_rows
from ..models import GovEvent, Member, TickerBar, TickerMeta, TickerPrice, TickerQuote, Trade

router = APIRouter()


@router.get("/tickers")
def top_tickers(db: Session = Depends(get_db), limit: int = Query(50, le=200)):
    rows = db.execute(
        select(Trade.ticker, func.count())
        .where(Trade.ticker.isnot(None))
        .group_by(Trade.ticker)
        .order_by(func.count().desc())
        .limit(limit)
    ).all()
    return {"items": [{"ticker": tk, "count": c} for tk, c in rows]}


@router.get("/tickers/{symbol}")
def ticker_detail(symbol: str, db: Session = Depends(get_db), limit: int = Query(500, le=1000)):
    sym = symbol.upper()
    rows = db.execute(
        select(Trade, Member)
        .join(Member, Trade.member_id == Member.id, isouter=True)
        .where(Trade.ticker == sym)
        .order_by(Trade.transaction_date.desc().nullslast(), Trade.id.desc())
        .limit(limit)
    ).all()
    by_type = dict(
        db.execute(
            select(Trade.transaction_type, func.count())
            .where(Trade.ticker == sym)
            .group_by(Trade.transaction_type)
        ).all()
    )
    meta = db.get(TickerMeta, sym)
    price = db.get(TickerPrice, sym)
    quote = db.get(TickerQuote, sym)
    return {
        "ticker": sym,
        "company": meta.company if meta else None,
        "sector": meta.sector if meta else None,
        "sentiment": float(meta.sentiment) if (meta and meta.sentiment is not None) else None,
        "sentiment_n": meta.sentiment_n if meta else None,
        "price": float(price.close) if (price and price.close is not None) else None,
        "price_as_of": price.as_of.isoformat() if (price and price.as_of) else None,
        "live_price": float(quote.last) if (quote and quote.last is not None) else None,
        "market_state": quote.market_state if quote else None,
        "count": len(rows),
        "by_transaction_type": by_type,
        "items": enrich_rows(db, rows),
    }


@router.get("/tickers/{symbol}/bars")
def ticker_bars(symbol: str, db: Session = Depends(get_db), days: int = Query(365, le=1825)):
    sym = symbol.upper()
    since = func.current_date() - days
    rows = db.execute(
        select(TickerBar.bar_date, TickerBar.close)
        .where(and_(TickerBar.ticker == sym, TickerBar.bar_date >= since))
        .order_by(TickerBar.bar_date)
    ).all()
    trades = db.execute(
        select(Trade.disclosure_date, Trade.transaction_type, Trade.amount_min, Trade.amount_max)
        .where(and_(Trade.ticker == sym, Trade.disclosure_date.isnot(None), Trade.disclosure_date >= since))
        .order_by(Trade.disclosure_date)
    ).all()
    return {
        "ticker": sym,
        "items": [{"date": d.isoformat(), "close": float(c)} for d, c in rows],
        "markers": [
            {
                "date": d.isoformat(),
                "transaction_type": typ,
                "amount_min": float(lo) if lo is not None else None,
                "amount_max": float(hi) if hi is not None else None,
            }
            for d, typ, lo, hi in trades
        ],
    }


@router.get("/tickers/{symbol}/events")
def ticker_events(symbol: str, db: Session = Depends(get_db), limit: int = Query(25, le=100)):
    sym = symbol.upper()
    rows = db.scalars(
        select(GovEvent)
        .where(GovEvent.ticker == sym)
        .order_by(GovEvent.filed_at.desc().nullslast())
        .limit(limit)
    ).all()
    return {
        "ticker": sym,
        "items": [
            {
                "id": e.id,
                "source": e.source,
                "form": e.form,
                "title": e.title,
                "url": e.url,
                "filed_at": e.filed_at.isoformat() if e.filed_at else None,
            }
            for e in rows
        ],
    }
