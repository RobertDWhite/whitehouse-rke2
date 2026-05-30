"""Daily price history from the free Yahoo Finance chart API (no key).

Stooq blocks bulk-history downloads from datacenter IPs; Yahoo's chart endpoint works and
returns full daily history as JSON. Stores closes in `ticker_bars` (incremental per ticker —
powers return-since-disclosure, performance leaderboards, SPY-benchmarked analytics) plus the
latest close in `ticker_prices`. Benchmarks (SPY, QQQ) are fetched as ordinary tickers."""
import datetime as dt
import time

from sqlalchemy import distinct, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import load_config
from app.db import SessionLocal, init_db
from app.models import TickerBar, TickerPrice, Trade

from . import common

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range={range}&interval=1d"
BENCHMARKS = ["SPY", "QQQ"]
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"


def run():
    cfg = load_config()
    init_db()
    sess = common.make_session(cfg)
    sess.headers.update({"User-Agent": BROWSER_UA})
    db = SessionLocal()
    updated = 0
    bars_added = 0
    try:
        tickers = [t for (t,) in db.execute(select(distinct(Trade.ticker)).where(Trade.ticker.isnot(None))).all()]
        symbols = list(dict.fromkeys(BENCHMARKS + tickers))
        last_bar = {
            tk: d
            for tk, d in db.execute(select(TickerBar.ticker, func.max(TickerBar.bar_date)).group_by(TickerBar.ticker)).all()
        }
        today = dt.date.today()

        for sym in symbols:
            # incremental: short range if we already have recent bars, else full backfill
            lb = last_bar.get(sym)
            rng = "1mo" if (lb and (today - lb).days < 25) else "2y"
            try:
                r = sess.get(CHART_URL.format(symbol=sym, range=rng), timeout=30)
                if r.status_code != 200:
                    continue
                res = (r.json().get("chart", {}).get("result") or [None])[0]
                if not res:
                    continue
                tstamps = res.get("timestamp") or []
                closes = (res.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
                latest = None
                for ts, cl in zip(tstamps, closes):
                    if cl is None:
                        continue
                    bd = dt.datetime.utcfromtimestamp(ts).date()
                    db.execute(
                        pg_insert(TickerBar)
                        .values(ticker=sym, bar_date=bd, close=float(cl))
                        .on_conflict_do_update(index_elements=["ticker", "bar_date"], set_={"close": float(cl)})
                    )
                    bars_added += 1
                    if latest is None or bd >= latest[0]:
                        latest = (bd, float(cl))
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
            time.sleep(0.2)
        common.record_run(db, "prices", rows_upserted=updated, success=True, note=f"{bars_added} bars")
        print(f"prices: {updated} tickers, {bars_added} bars")
    except Exception as e:  # noqa: BLE001
        common.record_run(db, "prices", success=False, note=str(e))
        raise
    finally:
        db.close()


if __name__ == "__main__":
    run()
