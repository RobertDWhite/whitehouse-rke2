"""Precompute follower-performance per trade: entry = first close on/after the DISCLOSURE date
(the date the public could act — honest, conservative, bakes in the up-to-45-day lag), return to
the latest close, and the same-window SPY benchmark. Powers the performance leaderboard.

These are PRICE returns of a hypothetical follower, not the member's actual return, and are
lagged — informational only."""
from sqlalchemy import text

from app.db import SessionLocal, init_db

from . import common

# entry price = first available daily bar on/after disclosure_date
SET_ENTRY = text(
    """
    UPDATE trades t SET entry_price = (
        SELECT b.close FROM ticker_bars b
        WHERE b.ticker = t.ticker AND b.bar_date >= t.disclosure_date
        ORDER BY b.bar_date LIMIT 1)
    WHERE t.ticker IS NOT NULL AND t.disclosure_date IS NOT NULL
    """
)

# return to latest close
SET_RETURN = text(
    """
    UPDATE trades t SET return_pct = (tp.close / t.entry_price - 1)
    FROM ticker_prices tp
    WHERE tp.ticker = t.ticker AND t.entry_price IS NOT NULL AND t.entry_price > 0
    """
)

# SPY return over the identical window (entry = first SPY bar on/after disclosure_date).
# Correlated subqueries in SET (Postgres forbids a FROM-clause LATERAL referencing the UPDATE target).
SET_BENCH = text(
    """
    UPDATE trades t SET bench_return_pct =
        (SELECT close FROM ticker_prices WHERE ticker = 'SPY')
        / NULLIF((SELECT b.close FROM ticker_bars b
                  WHERE b.ticker = 'SPY' AND b.bar_date >= t.disclosure_date
                  ORDER BY b.bar_date LIMIT 1), 0) - 1
    WHERE t.entry_price IS NOT NULL AND t.disclosure_date IS NOT NULL
    """
)


def run():
    init_db()
    db = SessionLocal()
    try:
        n1 = db.execute(SET_ENTRY).rowcount
        db.commit()
        n2 = db.execute(SET_RETURN).rowcount
        db.commit()
        n3 = db.execute(SET_BENCH).rowcount
        db.commit()
        common.record_run(db, "returns", rows_upserted=n2, success=True)
        print(f"returns: entry={n1} return={n2} bench={n3}")
    except Exception as e:  # noqa: BLE001
        common.record_run(db, "returns", success=False, note=str(e))
        raise
    finally:
        db.close()


if __name__ == "__main__":
    run()
