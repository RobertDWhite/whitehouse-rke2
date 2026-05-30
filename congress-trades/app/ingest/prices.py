"""Enrich tickers with a latest daily close from Stooq (free, no key) so the UI can show
implied share counts and return-since-disclosure. Per-symbol fetch (the multi-symbol batch
with f= is malformed), idempotent upsert."""
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

DEFAULT_QUOTE_URL = "https://stooq.com/q/l/?s={symbol}.us&f=sd2t2ohlcv&e=csv"


def run():
    cfg = load_config()
    init_db()
    QUOTE_URL = cfg.get("prices", {}).get("quote_url", DEFAULT_QUOTE_URL)
    sess = common.make_session(cfg)
    db = SessionLocal()
    updated = 0
    try:
        tickers = [t for (t,) in db.execute(select(distinct(Trade.ticker)).where(Trade.ticker.isnot(None))).all()]
        # Stooq's multi-symbol batch with f= returns a malformed single row, so fetch per symbol.
        # Single light quote (headerless): SYMBOL.US,YYYY-MM-DD,HH:MM:SS,open,high,low,close,volume
        for n, t in enumerate(tickers):
            try:
                r = sess.get(QUOTE_URL.format(symbol=t.lower()), timeout=15)
                if r.status_code != 200:
                    continue
                cols = next(csv.reader(io.StringIO(r.text)), [])
                if len(cols) < 7 or cols[6] in ("", "N/D"):
                    continue
                try:
                    close_f = float(cols[6])
                except ValueError:
                    continue
                date = cols[1]
                stmt = pg_insert(TickerPrice).values(
                    ticker=t.upper(),
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
                if updated % 50 == 0:
                    db.commit()
            except Exception as e:  # noqa: BLE001
                print(f"prices: {t} failed: {e}")
            time.sleep(0.3)
        db.commit()
        common.record_run(db, "prices", rows_upserted=updated, success=True)
        print(f"prices: updated {updated} tickers")
    except Exception as e:  # noqa: BLE001
        common.record_run(db, "prices", success=False, note=str(e))
        raise
    finally:
        db.close()


if __name__ == "__main__":
    run()
