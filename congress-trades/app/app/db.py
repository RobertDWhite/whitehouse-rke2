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
    # create_all does not ALTER existing tables — add later columns idempotently
    with engine.begin() as conn:
        for col, typ in (
            ("net_worth_min", "NUMERIC"),
            ("net_worth_max", "NUMERIC"),
            ("net_worth_year", "INTEGER"),
        ):
            conn.execute(text(f"ALTER TABLE members ADD COLUMN IF NOT EXISTS {col} {typ}"))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
