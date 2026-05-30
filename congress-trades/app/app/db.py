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
            "ALTER TABLE ai_summaries ADD COLUMN IF NOT EXISTS watchlist JSONB",
        ):
            conn.execute(text(stmt))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
