import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATABASE_URL = os.environ["DATABASE_URL"]

engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=5, max_overflow=10)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def init_db():
    from . import models  # noqa: F401  (register models on Base)
    from sqlalchemy import text

    Base.metadata.create_all(engine)
    # create_all does not ALTER existing tables — add later columns/indexes idempotently
    with engine.begin() as conn:
        for col, typ in (
            ("net_worth_min", "NUMERIC"),
            ("net_worth_max", "NUMERIC"),
            ("net_worth_year", "INTEGER"),
            ("bioguide", "VARCHAR(16)"),
            ("committees", "JSONB"),
            ("committee_sectors", "JSONB"),
        ):
            conn.execute(text(f"ALTER TABLE members ADD COLUMN IF NOT EXISTS {col} {typ}"))
        for stmt in (
            "CREATE INDEX IF NOT EXISTS ix_trades_member_txdate ON trades (member_id, transaction_date)",
            "CREATE INDEX IF NOT EXISTS ix_trades_ticker_txdate ON trades (ticker, transaction_date)",
            "CREATE INDEX IF NOT EXISTS ix_trades_disclosure_date ON trades (disclosure_date)",
            "CREATE INDEX IF NOT EXISTS ix_members_bioguide ON members (bioguide)",
            "ALTER TABLE trades ADD COLUMN IF NOT EXISTS entry_price NUMERIC",
            "ALTER TABLE trades ADD COLUMN IF NOT EXISTS return_pct NUMERIC",
            "ALTER TABLE trades ADD COLUMN IF NOT EXISTS bench_return_pct NUMERIC",
            "ALTER TABLE trades ADD COLUMN IF NOT EXISTS option_type VARCHAR(8)",
            "ALTER TABLE trades ADD COLUMN IF NOT EXISTS option_strike NUMERIC",
            "ALTER TABLE trades ADD COLUMN IF NOT EXISTS option_expiration DATE",
            "ALTER TABLE ai_summaries ADD COLUMN IF NOT EXISTS watchlist JSONB",
            "ALTER TABLE ticker_meta ADD COLUMN IF NOT EXISTS sentiment NUMERIC",
            "ALTER TABLE ticker_meta ADD COLUMN IF NOT EXISTS sentiment_n INTEGER",
            "ALTER TABLE ticker_quotes ADD COLUMN IF NOT EXISTS provider VARCHAR(32)",
            "ALTER TABLE trade_reconciliation ADD COLUMN IF NOT EXISTS confidence NUMERIC",
            "ALTER TABLE trade_reconciliation ADD COLUMN IF NOT EXISTS status VARCHAR(16) DEFAULT 'open'",
            "ALTER TABLE trade_reconciliation ADD COLUMN IF NOT EXISTS resolution_note TEXT",
            "ALTER TABLE trade_reconciliation ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMP WITH TIME ZONE",
            "CREATE INDEX IF NOT EXISTS ix_leg_events_bioguide ON legislative_events (bioguide)",
            "CREATE INDEX IF NOT EXISTS ix_leg_events_member ON legislative_events (member_id)",
            "CREATE INDEX IF NOT EXISTS ix_leg_events_ticker ON legislative_events (ticker)",
            "CREATE INDEX IF NOT EXISTS ix_leg_events_sector ON legislative_events (sector)",
            "CREATE INDEX IF NOT EXISTS ix_leg_events_occurred ON legislative_events (occurred_at)",
            "CREATE INDEX IF NOT EXISTS ix_recon_kind ON trade_reconciliation (kind)",
            "CREATE INDEX IF NOT EXISTS ix_recon_status ON trade_reconciliation (status)",
            "CREATE INDEX IF NOT EXISTS ix_ticker_alias_ticker ON ticker_aliases (ticker)",
            # Scrub OCR/parse year-mangling: future dates or a transaction past its own disclosure.
            "UPDATE trades SET transaction_date = NULL WHERE transaction_date > CURRENT_DATE OR (disclosure_date IS NOT NULL AND transaction_date > disclosure_date)",
            "UPDATE trades SET disclosure_date = NULL WHERE disclosure_date > CURRENT_DATE",
        ):
            conn.execute(text(stmt))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
