"""Delivery surfaces: CSV/JSON export, RSS, ingest status, and per-ticker news (Google News RSS)."""
import csv
import datetime as dt
import io
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

import requests
from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse, Response, StreamingResponse
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import IngestState, Member, TickerMeta, Trade, TradeSignal

router = APIRouter()

DISCLAIMER = "Publicly disclosed congressional trades (STOCK Act), lagged up to 45 days. Informational, not advice."
_EXPORT_COLS = ["transaction_date", "disclosure_date", "member", "party", "state", "chamber",
                "ticker", "transaction_type", "amount_range", "source"]


@router.get("/export/trades.csv")
def export_csv(db: Session = Depends(get_db), limit: int = Query(5000, le=50000)):
    rows = db.execute(
        select(Trade, Member).join(Member, Member.id == Trade.member_id, isouter=True)
        .order_by(Trade.disclosure_date.desc().nullslast()).limit(limit)
    ).all()

    def gen():
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(_EXPORT_COLS)
        yield buf.getvalue(); buf.seek(0); buf.truncate(0)
        for t, m in rows:
            w.writerow([t.transaction_date, t.disclosure_date, m.full_name if m else "", m.party if m else "",
                        m.state if m else "", t.chamber, t.ticker, t.transaction_type, t.amount_range_raw, t.source])
            yield buf.getvalue(); buf.seek(0); buf.truncate(0)

    return StreamingResponse(gen(), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=congress_trades.csv", "X-Disclaimer": DISCLAIMER})


@router.get("/feed.rss")
def rss(db: Session = Depends(get_db), limit: int = Query(50, le=200)):
    rows = db.execute(
        select(Trade, Member, func.array_agg(TradeSignal.signal_type))
        .join(Member, Member.id == Trade.member_id, isouter=True)
        .join(TradeSignal, TradeSignal.trade_id == Trade.id, isouter=True)
        .group_by(Trade.id, Member.id)
        .order_by(Trade.disclosure_date.desc().nullslast(), Trade.id.desc())
        .limit(limit)
    ).all()
    items = []
    for t, m, sigs in rows:
        who = m.full_name if m else "Unknown"
        title = f"{who} {(t.transaction_type or '').upper()} {t.ticker or t.asset_name or '?'} {t.amount_range_raw or ''}"
        desc = f"Disclosed {t.disclosure_date}. Signals: {', '.join(s for s in (sigs or []) if s) or 'none'}. {DISCLAIMER}"
        items.append(
            f"<item><title>{escape(title)}</title><description>{escape(desc)}</description>"
            f"<guid isPermaLink='false'>congress-trade-{t.id}</guid>"
            f"<pubDate>{t.disclosure_date}</pubDate></item>"
        )
    xml = (f"<?xml version='1.0'?><rss version='2.0'><channel>"
           f"<title>Congress Trades</title><link>https://congress.white.fm</link>"
           f"<description>{escape(DISCLAIMER)}</description>{''.join(items)}</channel></rss>")
    return Response(content=xml, media_type="application/rss+xml")


@router.get("/status")
def status(db: Session = Depends(get_db)):
    now = dt.datetime.now(dt.timezone.utc)
    expected = {
        "house": 60 * 60,
        "senate": 90 * 60,
        "lambda": 18 * 60 * 60,
        "quotes": 30 * 60,
        "prices": 36 * 60 * 60,
        "signals": 2 * 60 * 60,
        "gov_events": 2 * 60 * 60,
        "legislative_events": 48 * 60 * 60,
        "reconciliation": 24 * 60 * 60,
    }
    sources = []
    for st in db.scalars(select(IngestState)).all():
        age = (now - st.last_success).total_seconds() if st.last_success else None
        max_age = expected.get(st.source.split(":", 1)[0], 24 * 60 * 60)
        sources.append({"source": st.source, "last_success": st.last_success.isoformat() if st.last_success else None,
                        "age_seconds": age, "max_age_seconds": max_age,
                        "stale": age is None or age > max_age,
                        "rows": st.rows_upserted, "note": st.note})
    latest = db.scalar(select(func.max(Trade.disclosure_date)))
    return {
        "trades": db.scalar(select(func.count(Trade.id))) or 0,
        "members": db.scalar(select(func.count(Member.id))) or 0,
        "latest_disclosure": latest.isoformat() if latest else None,
        "stale_sources": sum(1 for s in sources if s["stale"]),
        "sources": sources,
    }


@router.get("/tickers/{symbol}/news")
def ticker_news(symbol: str, limit: int = Query(8, le=20)):
    """Recent headlines for a ticker via Google News RSS (free, no key)."""
    sym = symbol.upper()
    url = f"https://news.google.com/rss/search?q={sym}+stock&hl=en-US&gl=US&ceid=US:en"
    out = []
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=12)
        root = ET.fromstring(r.text)
        for item in root.findall(".//item")[:limit]:
            out.append({
                "title": (item.findtext("title") or "").rsplit(" - ", 1)[0],
                "source": (item.findtext("title") or "").rsplit(" - ", 1)[-1],
                "link": item.findtext("link"),
                "pub_date": item.findtext("pubDate"),
            })
    except Exception:  # noqa: BLE001
        pass
    return {"ticker": sym, "items": out}
