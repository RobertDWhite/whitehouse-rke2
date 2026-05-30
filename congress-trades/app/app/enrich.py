"""Attach signals, latest price, and source-filing URL to a page of (Trade, Member) rows
in a few batched queries (avoids N+1)."""
from sqlalchemy import select

from .models import Filing, TickerPrice, TradeSignal
from .serialize import trade_dict


def enrich_rows(db, rows):
    ids = [t.id for t, _ in rows]
    fids = {t.filing_id for t, _ in rows if t.filing_id}
    tks = {t.ticker for t, _ in rows if t.ticker}

    sig_map = {}
    if ids:
        for tid, stype, score, detail in db.execute(
            select(TradeSignal.trade_id, TradeSignal.signal_type, TradeSignal.score, TradeSignal.detail).where(
                TradeSignal.trade_id.in_(ids)
            )
        ).all():
            sig_map.setdefault(tid, []).append({"type": stype, "score": score, "detail": detail})

    filing_map = {}
    if fids:
        for fid, url in db.execute(select(Filing.id, Filing.source_url).where(Filing.id.in_(fids))).all():
            filing_map[fid] = url

    price_map = {}
    if tks:
        for tk, close in db.execute(
            select(TickerPrice.ticker, TickerPrice.close).where(TickerPrice.ticker.in_(tks))
        ).all():
            price_map[tk] = float(close) if close is not None else None

    out = []
    for t, m in rows:
        d = trade_dict(t, m, price=price_map.get(t.ticker))
        d["source_url"] = filing_map.get(t.filing_id)
        all_sigs = sig_map.get(t.id, [])
        # conviction is a 0-100 display score, not an alert badge — surface it separately
        conv = next((s for s in all_sigs if s["type"] == "conviction"), None)
        badges = [s for s in all_sigs if s["type"] != "conviction"]
        d["signals"] = badges
        d["signal_score"] = sum(s["score"] for s in badges)
        d["conviction"] = conv["score"] if conv else None
        d["conviction_detail"] = conv["detail"] if conv else None
        # follower performance (lagged; entry = close on/after disclosure)
        d["return_pct"] = float(t.return_pct) if t.return_pct is not None else None
        d["bench_return_pct"] = float(t.bench_return_pct) if t.bench_return_pct is not None else None
        d["excess_pct"] = (
            float(t.return_pct - t.bench_return_pct)
            if (t.return_pct is not None and t.bench_return_pct is not None)
            else None
        )
        d["entry_price"] = float(t.entry_price) if t.entry_price is not None else None
        out.append(d)
    return out
