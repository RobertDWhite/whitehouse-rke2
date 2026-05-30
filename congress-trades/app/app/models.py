import datetime as dt

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def _now():
    return dt.datetime.now(dt.timezone.utc)


class Member(Base):
    __tablename__ = "members"

    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(256))
    name_norm: Mapped[str] = mapped_column(String(256), unique=True, index=True)
    bioguide: Mapped[str | None] = mapped_column(String(16), index=True)  # join key to congress.gov
    chamber: Mapped[str | None] = mapped_column(String(16))
    party: Mapped[str | None] = mapped_column(String(32))
    state: Mapped[str | None] = mapped_column(String(8))
    district: Mapped[str | None] = mapped_column(String(8))
    # Estimated net worth from the latest annual Financial Disclosure (asset-range
    # midpoints minus liabilities). Range because disclosures are reported as $ brackets.
    net_worth_min: Mapped[int | None] = mapped_column(Numeric)
    net_worth_max: Mapped[int | None] = mapped_column(Numeric)
    net_worth_year: Mapped[int | None] = mapped_column(Integer)
    # committee memberships (from unitedstates/congress-legislators) + derived oversight sectors
    committees: Mapped[list | None] = mapped_column(JSON)
    committee_sectors: Mapped[list | None] = mapped_column(JSON)


class Filing(Base):
    __tablename__ = "filings"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(32), index=True)  # house | senate
    doc_id: Mapped[str] = mapped_column(String(64), index=True)
    chamber: Mapped[str | None] = mapped_column(String(16))
    member_id: Mapped[int | None] = mapped_column(ForeignKey("members.id"))
    filing_type: Mapped[str | None] = mapped_column(String(8))
    filing_date: Mapped[dt.date | None] = mapped_column(Date)
    source_url: Mapped[str | None] = mapped_column(Text)
    # pending | parsed | ocr | paper | error
    parse_status: Mapped[str] = mapped_column(String(16), default="pending")
    raw_text: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (UniqueConstraint("source", "doc_id", name="uq_filing_source_doc"),)


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(primary_key=True)
    filing_id: Mapped[int | None] = mapped_column(ForeignKey("filings.id"))
    source: Mapped[str] = mapped_column(String(32), index=True)  # house_primary | senate_primary | lambda
    source_priority: Mapped[int] = mapped_column(Integer, default=1)
    member_id: Mapped[int | None] = mapped_column(ForeignKey("members.id"), index=True)
    chamber: Mapped[str | None] = mapped_column(String(16), index=True)
    transaction_date: Mapped[dt.date | None] = mapped_column(Date, index=True)
    disclosure_date: Mapped[dt.date | None] = mapped_column(Date)
    owner: Mapped[str | None] = mapped_column(String(32))
    ticker: Mapped[str | None] = mapped_column(String(32), index=True)
    asset_name: Mapped[str | None] = mapped_column(Text)
    asset_type: Mapped[str | None] = mapped_column(String(64))
    transaction_type: Mapped[str | None] = mapped_column(String(16), index=True)
    amount_min: Mapped[int | None] = mapped_column(Numeric)
    amount_max: Mapped[int | None] = mapped_column(Numeric)
    amount_range_raw: Mapped[str | None] = mapped_column(String(64))
    cap_gains_over_200: Mapped[bool | None] = mapped_column(Boolean)
    comment: Mapped[str | None] = mapped_column(Text)
    dedup_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # follower performance: entry = close on/after disclosure_date (the date you could act),
    # return to latest close, and the same-window SPY benchmark. Precomputed nightly.
    entry_price: Mapped[float | None] = mapped_column(Numeric)
    return_pct: Mapped[float | None] = mapped_column(Numeric)
    bench_return_pct: Mapped[float | None] = mapped_column(Numeric)

    __table_args__ = (
        # dominant access patterns: member detail and ticker detail, newest-first
        Index("ix_trades_member_txdate", "member_id", "transaction_date"),
        Index("ix_trades_ticker_txdate", "ticker", "transaction_date"),
        Index("ix_trades_disclosure_date", "disclosure_date"),
    )


class IngestState(Base):
    """Per-source incremental-fetch state (conditional GET / change detection)."""

    __tablename__ = "ingest_state"

    source: Mapped[str] = mapped_column(String(64), primary_key=True)  # e.g. house:2026
    etag: Mapped[str | None] = mapped_column(String(256))
    last_modified: Mapped[str | None] = mapped_column(String(128))
    content_length: Mapped[int | None] = mapped_column(Integer)
    last_success: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_run: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    rows_upserted: Mapped[int | None] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(Text)


class TradeSignal(Base):
    """A scored 'interesting' attribute of a trade (cluster buy, large, options, lag, …)."""

    __tablename__ = "trade_signals"

    id: Mapped[int] = mapped_column(primary_key=True)
    trade_id: Mapped[int] = mapped_column(ForeignKey("trades.id", ondelete="CASCADE"), index=True)
    signal_type: Mapped[str] = mapped_column(String(32), index=True)
    score: Mapped[int] = mapped_column(Integer, default=1)
    detail: Mapped[dict | None] = mapped_column(JSON)
    alerted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)

    __table_args__ = (UniqueConstraint("trade_id", "signal_type", name="uq_signal_trade_type"),)


class Watchlist(Base):
    __tablename__ = "watchlist"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))  # member | ticker
    value: Mapped[str] = mapped_column(String(64))  # member_id (str) or ticker
    min_score: Mapped[int] = mapped_column(Integer, default=1)

    __table_args__ = (UniqueConstraint("kind", "value", name="uq_watch_kind_value"),)


class TickerPrice(Base):
    """Latest daily close per ticker (Stooq), for return-since-disclosure / share counts."""

    __tablename__ = "ticker_prices"

    ticker: Mapped[str] = mapped_column(String(32), primary_key=True)
    close: Mapped[float | None] = mapped_column(Numeric)
    as_of: Mapped[dt.date | None] = mapped_column(Date)
    updated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class TickerBar(Base):
    """Historical daily closes (Stooq) — enables entry-price/return-since-disclosure, leaderboards,
    and benchmarking vs SPY. Benchmarks (SPY/QQQ) are stored as normal tickers."""

    __tablename__ = "ticker_bars"

    ticker: Mapped[str] = mapped_column(String(32), primary_key=True)
    bar_date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    close: Mapped[float] = mapped_column(Numeric)


class TickerMeta(Base):
    """Sector/industry/company metadata from SEC (company_tickers + submissions)."""

    __tablename__ = "ticker_meta"

    ticker: Mapped[str] = mapped_column(String(32), primary_key=True)
    cik: Mapped[str | None] = mapped_column(String(16), index=True)
    company: Mapped[str | None] = mapped_column(String(256))
    sic: Mapped[str | None] = mapped_column(String(8))
    sector: Mapped[str | None] = mapped_column(String(64), index=True)
    updated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class AiSummary(Base):
    __tablename__ = "ai_summaries"

    id: Mapped[int] = mapped_column(primary_key=True)
    scope: Mapped[str] = mapped_column(String(32), index=True)  # global | member
    member_id: Mapped[int | None] = mapped_column(ForeignKey("members.id"), index=True)
    window_days: Mapped[int] = mapped_column(Integer)
    summary_md: Mapped[str | None] = mapped_column(Text)
    observations: Mapped[list | None] = mapped_column(JSON)
    watchlist: Mapped[list | None] = mapped_column(JSON)
    model: Mapped[str | None] = mapped_column(String(64))
    data_hash: Mapped[str | None] = mapped_column(String(64))
    trade_count: Mapped[int | None] = mapped_column(Integer)
    generated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
