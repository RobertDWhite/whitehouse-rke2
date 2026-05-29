"""Enrich tickers with a latest daily close from Stooq (free, no key) so the UI can show
implied share counts and return-since-disclosure. Batched, idempotent upsert."""
import csv
import datetime as dt
import io
import time

from sqlalchemy import distinct, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import load_config
from app.db import SessionLocal, init_db
from app.models import TickerPrice, Trade

from . import common

BATCH = 40
# multi-symbol light quote; one CSV row per symbol: Symbol,Date,Time,Open,High,Low,Close,Volume
BATCH_URL = "https://stooq.com/q/l/?s={symbols}&f=sd2t2ohlcv&e=csv"


def run():
    cfg = load_config()
    init_db()
    sess = common.make_session(cfg)
    db = SessionLocal()
    updated = 0
    try:
        tickers = [t for (t,) in db.execute(select(distinct(Trade.ticker)).where(Trade.ticker.isnot(None))).all()]
        for i in range(0, len(tickers), BATCH):
            chunk = tickers[i : i + BATCH]
            symbols = ",".join(f"{t.lower()}.us" for t in chunk)
            try:
                r = sess.get(BATCH_URL.format(symbols=symbols), timeout=30)
                if r.status_code != 200:
                    continue
                reader = csv.DictReader(io.StringIO(r.text))
                for row in reader:
                    sym = (row.get("Symbol") or "").split(".")[0].upper()
                    close = row.get("Close")
                    date = row.get("Date")
                    if not sym or close in (None, "", "N/D"):
                        continue
                    try:
                        close_f = float(close)
                    except ValueError:
                        continue
                    stmt = pg_insert(TickerPrice).values(
                        ticker=sym,
                        close=close_f,
                        as_of=dt.datetime.strptime(date, "%Y-%m-%d").date() if date and date != "N/D" else None,
                        updated_at=dt.datetime.now(dt.timezone.utc),
                    )
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["ticker"],
                        set_={"close": stmt.excluded.close, "as_of": stmt.excluded.as_of, "updated_at": stmt.excluded.updated_at},
                    )
                    db.execute(stmt)
                    updated += 1
                db.commit()
            except Exception as e:  # noqa: BLE001
                print(f"prices: chunk {i} failed: {e}")
            time.sleep(1)
        common.record_run(db, "prices", rows_upserted=updated, success=True)
        print(f"prices: updated {updated} tickers")
    except Exception as e:  # noqa: BLE001
        common.record_run(db, "prices", success=False, note=str(e))
        raise
    finally:
        db.close()


if __name__ == "__main__":
    run()
