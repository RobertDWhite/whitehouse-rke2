"""Retail sentiment per ticker from StockTwits (free, no key, has built-in bull/bear labels).

Rate-limited (~200 req/hr unauth), so only the most-actively-traded tickers are refreshed each
run. Stored on ticker_meta (-1 bearish .. +1 bullish) for the ticker page + AI context."""
import datetime as dt
import time

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import load_config
from app.db import SessionLocal, init_db
from app.models import TickerMeta, Trade

from . import common

URL = "https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json"
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"


def run():
    cfg = load_config()
    init_db()
    limit = int(cfg.get("sentiment", {}).get("max_tickers", 120))
    sess = common.make_session(cfg)
    sess.headers.update({"User-Agent": BROWSER_UA})
    db = SessionLocal()
    n = 0
    try:
        tickers = [
            tk
            for (tk, _) in db.execute(
                select(Trade.ticker, func.count())
                .where(Trade.ticker.isnot(None))
                .group_by(Trade.ticker)
                .order_by(func.count().desc())
                .limit(limit)
            ).all()
        ]
        for tk in tickers:
            try:
                r = sess.get(URL.format(ticker=tk), timeout=15)
                if r.status_code != 200:
                    if r.status_code == 429:
                        print("sentiment: rate-limited, stopping")
                        break
                    continue
                msgs = r.json().get("messages", []) or []
                bull = bear = 0
                for m in msgs:
                    basic = ((m.get("entities") or {}).get("sentiment") or {}).get("basic")
                    if basic == "Bullish":
                        bull += 1
                    elif basic == "Bearish":
                        bear += 1
                tot = bull + bear
                score = (bull - bear) / tot if tot else None
                if score is not None:
                    db.execute(
                        pg_insert(TickerMeta)
                        .values(ticker=tk, sentiment=score, sentiment_n=tot, updated_at=dt.datetime.now(dt.timezone.utc))
                        .on_conflict_do_update(index_elements=["ticker"], set_={"sentiment": score, "sentiment_n": tot})
                    )
                    n += 1
                    if n % 25 == 0:
                        db.commit()
            except Exception as e:  # noqa: BLE001
                db.rollback()
                print(f"sentiment: {tk} failed: {e}")
            time.sleep(0.4)
        db.commit()
        common.record_run(db, "sentiment", rows_upserted=n, success=True)
        print(f"sentiment: scored {n} tickers")
    except Exception as e:  # noqa: BLE001
        common.record_run(db, "sentiment", success=False, note=str(e))
        raise
    finally:
        db.close()


if __name__ == "__main__":
    run()
