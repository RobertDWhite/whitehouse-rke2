"""Enrich tickers with daily price history from Stooq (free, no key).

Stores full daily closes in `ticker_bars` (incremental per ticker) — this powers
return-since-disclosure, performance leaderboards, and SPY-benchmarked backtests — plus the
latest close in `ticker_prices` for quick share-count math. Benchmarks (SPY, QQQ) are fetched
as ordinary tickers. The multi-symbol batch quote is malformed, so we fetch per symbol."""
import csv
import datetime as dt
import io
import time

from sqlalchemy import distinct, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import load_config
from app.db import SessionLocal, init_db
from app.models import TickerBar, TickerPrice, Trade

from . import common

# daily history (has a header row: Date,Open,High,Low,Close,Volume)
DEFAULT_HISTORY_URL = "https://stooq.com/q/d/l/?s={symbol}.us&d1={d1}&i=d"
BENCHMARKS = ["SPY", "QQQ"]
DEFAULT_START = "20230101"


def run():
    cfg = load_config()
    init_db()
    pc = cfg.get("prices", {})
    history_url = pc.get("history_url", DEFAULT_HISTORY_URL)
    start = pc.get("history_start", DEFAULT_START)
    sess = common.make_session(cfg)
    db = SessionLocal()
    updated = 0
    bars_added = 0
    try:
        tickers = [t for (t,) in db.execute(select(distinct(Trade.ticker)).where(Trade.ticker.isnot(None))).all()]
        symbols = list(dict.fromkeys(BENCHMARKS + tickers))  # benchmarks first, dedup

        # last stored bar per ticker (for incremental fetch)
        last_bar = {
            tk: d
            for tk, d in db.execute(select(TickerBar.ticker, func.max(TickerBar.bar_date)).group_by(TickerBar.ticker)).all()
        }

        for sym in symbols:
            d1 = start
            if sym in last_bar and last_bar[sym]:
                d1 = last_bar[sym].strftime("%Y%m%d")
            try:
                r = sess.get(history_url.format(symbol=sym.lower(), d1=d1), timeout=30)
                if r.status_code != 200:
                    continue
                rows = list(csv.DictReader(io.StringIO(r.text)))
                latest = None
                for row in rows:
                    date = row.get("Date")
                    close = row.get("Close")
                    if not date or close in (None, "", "N/D"):
                        continue
                    try:
                        bd = dt.datetime.strptime(date, "%Y-%m-%d").date()
                        cl = float(close)
                    except ValueError:
                        continue
                    db.execute(
                        pg_insert(TickerBar)
                        .values(ticker=sym, bar_date=bd, close=cl)
                        .on_conflict_do_update(index_elements=["ticker", "bar_date"], set_={"close": cl})
                    )
                    bars_added += 1
                    if latest is None or bd >= latest[0]:
                        latest = (bd, cl)
                if latest:
                    db.execute(
                        pg_insert(TickerPrice)
                        .values(ticker=sym, close=latest[1], as_of=latest[0], updated_at=dt.datetime.now(dt.timezone.utc))
                        .on_conflict_do_update(
                            index_elements=["ticker"],
                            set_={"close": latest[1], "as_of": latest[0], "updated_at": dt.datetime.now(dt.timezone.utc)},
                        )
                    )
                    updated += 1
                db.commit()
            except Exception as e:  # noqa: BLE001
                print(f"prices: {sym} failed: {e}")
                db.rollback()
            time.sleep(0.25)
        common.record_run(db, "prices", rows_upserted=updated, success=True, note=f"{bars_added} bars")
        print(f"prices: {updated} tickers, {bars_added} bars upserted")
    except Exception as e:  # noqa: BLE001
        common.record_run(db, "prices", success=False, note=str(e))
        raise
    finally:
        db.close()


if __name__ == "__main__":
    run()
