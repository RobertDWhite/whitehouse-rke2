"""Live-ish last price from the Yahoo chart API (1m), for live return-since-disclosure.

Cheaper than full history: one request per ticker, reads meta.regularMarketPrice + marketState.
Runs frequently (every ~10 min). Falls back gracefully per ticker."""
import datetime as dt
import time

from sqlalchemy import distinct, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import load_config
from app.db import SessionLocal, init_db
from app.models import TickerQuote, Trade

from . import common

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1d&interval=1m"
BENCHMARKS = ["SPY", "QQQ", "NANC", "KRUZ"]
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"


def run():
    cfg = load_config()
    init_db()
    sess = common.make_session(cfg)
    sess.headers.update({"User-Agent": BROWSER_UA})
    db = SessionLocal()
    n = 0
    try:
        tickers = [t for (t,) in db.execute(select(distinct(Trade.ticker)).where(Trade.ticker.isnot(None))).all()]
        for sym in dict.fromkeys(BENCHMARKS + tickers):
            try:
                r = sess.get(CHART_URL.format(symbol=sym), timeout=15)
                if r.status_code != 200:
                    continue
                meta = ((r.json().get("chart", {}).get("result") or [{}])[0] or {}).get("meta") or {}
                last = meta.get("regularMarketPrice")
                if last is None:
                    continue
                db.execute(
                    pg_insert(TickerQuote)
                    .values(ticker=sym, last=float(last), market_state=meta.get("marketState"), as_of=dt.datetime.now(dt.timezone.utc))
                    .on_conflict_do_update(
                        index_elements=["ticker"],
                        set_={"last": float(last), "market_state": meta.get("marketState"), "as_of": dt.datetime.now(dt.timezone.utc)},
                    )
                )
                n += 1
                if n % 50 == 0:
                    db.commit()
            except Exception as e:  # noqa: BLE001
                db.rollback()
                print(f"quotes: {sym} failed: {e}")
            time.sleep(0.15)
        db.commit()
        common.record_run(db, "quotes", rows_upserted=n, success=True)
        print(f"quotes: {n} tickers")
    except Exception as e:  # noqa: BLE001
        common.record_run(db, "quotes", success=False, note=str(e))
        raise
    finally:
        db.close()


if __name__ == "__main__":
    run()
