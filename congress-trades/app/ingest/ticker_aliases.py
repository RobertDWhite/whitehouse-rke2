"""Normalize null-ticker trades using SEC company title aliases.

This is intentionally conservative: it only fills a ticker when the disclosed asset name contains
a known SEC company title or the title contains the asset name. Ambiguous aliases are avoided by
the primary-key alias table; reconciliation still flags low-confidence gaps for humans.
"""
import datetime as dt
import re

_SPACE = re.compile(r"\s+")
_CORP_WORDS = re.compile(r"\b(inc|corp|corporation|class|common|stock|ordinary|shares|plc|ltd|co|company)\b", re.I)


def _norm(text):
    text = _CORP_WORDS.sub(" ", (text or "").lower())
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    return _SPACE.sub(" ", text).strip()


def _match(asset, aliases):
    na = _norm(asset)
    if len(na) < 4:
        return None
    for alias, ticker, conf in aliases:
        al = _norm(alias)
        if len(al) < 4:
            continue
        if al in na or na in al:
            return ticker, float(conf or 0.8), alias
    return None


def run():
    from sqlalchemy import select

    from app.db import SessionLocal, init_db
    from app.models import TickerAlias, Trade

    from . import common

    init_db()
    db = SessionLocal()
    n = 0
    try:
        aliases = db.execute(select(TickerAlias.alias, TickerAlias.ticker, TickerAlias.confidence).order_by(TickerAlias.confidence.desc().nullslast())).all()
        rows = db.scalars(
            select(Trade)
            .where(Trade.ticker.is_(None), Trade.asset_name.isnot(None), Trade.source.in_(["house_primary", "senate_primary"]))
            .limit(1000)
        ).all()
        for t in rows:
            m = _match(t.asset_name, aliases)
            if not m:
                continue
            ticker, conf, alias = m
            if conf < 0.9:
                continue
            t.ticker = ticker
            note = f"ticker_normalized={ticker} from alias={alias}"
            t.comment = f"{t.comment or ''} | {note}".strip(" |")
            n += 1
        db.commit()
        common.record_run(db, "ticker_aliases", rows_upserted=n, success=True)
        print(f"ticker_aliases: normalized {n} trades")
    except Exception as e:  # noqa: BLE001
        common.record_run(db, "ticker_aliases", success=False, note=str(e))
        raise
    finally:
        db.close()


if __name__ == "__main__":
    run()
