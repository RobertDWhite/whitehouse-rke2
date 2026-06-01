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
    option_type: Mapped[str | None] = mapped_column(String(8))  # call | put
    option_strike: Mapped[float | None] = mapped_column(Numeric)
    option_expiration: Mapped[dt.date | None] = mapped_column(Date)

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
    """Sector/industry/company metadata from SEC (company_tickers + submissions) + sentiment."""

    __tablename__ = "ticker_meta"

    ticker: Mapped[str] = mapped_column(String(32), primary_key=True)
    cik: Mapped[str | None] = mapped_column(String(16), index=True)
    company: Mapped[str | None] = mapped_column(String(256))
    sic: Mapped[str | None] = mapped_column(String(8))
    sector: Mapped[str | None] = mapped_column(String(64), index=True)
    sentiment: Mapped[float | None] = mapped_column(Numeric)  # StockTwits bull-bear ratio -1..1
    sentiment_n: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class TickerAlias(Base):
    """Best-effort aliases for mapping disclosed asset names to canonical tickers."""

    __tablename__ = "ticker_aliases"

    alias: Mapped[str] = mapped_column(String(256), primary_key=True)
    ticker: Mapped[str] = mapped_column(String(32), index=True)
    source: Mapped[str | None] = mapped_column(String(32))
    confidence: Mapped[float | None] = mapped_column(Numeric)
    updated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class TickerQuote(Base):
    """Live-ish last price (Yahoo 1m) for live return-since-disclosure."""

    __tablename__ = "ticker_quotes"

    ticker: Mapped[str] = mapped_column(String(32), primary_key=True)
    last: Mapped[float | None] = mapped_column(Numeric)
    market_state: Mapped[str | None] = mapped_column(String(16))
    provider: Mapped[str | None] = mapped_column(String(32))
    as_of: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class GovEvent(Base):
    """SEC EDGAR near-real-time filings (Form 4 insider, 8-K) keyed to a ticker via CIK."""

    __tablename__ = "gov_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(32))  # edgar
    form: Mapped[str | None] = mapped_column(String(16), index=True)  # 4 | 8-K
    cik: Mapped[str | None] = mapped_column(String(16), index=True)
    ticker: Mapped[str | None] = mapped_column(String(32), index=True)
    title: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    filed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    accession: Mapped[str] = mapped_column(String(32), unique=True)


class LegislativeEvent(Base):
    """Congress.gov context near a trade: bills, votes, amendments, and committee activity."""

    __tablename__ = "legislative_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(32), default="congress.gov")
    event_type: Mapped[str] = mapped_column(String(32), index=True)  # bill | vote | committee
    congress: Mapped[int | None] = mapped_column(Integer)
    chamber: Mapped[str | None] = mapped_column(String(16), index=True)
    bioguide: Mapped[str | None] = mapped_column(String(16), index=True)
    member_id: Mapped[int | None] = mapped_column(ForeignKey("members.id"), index=True)
    committee: Mapped[str | None] = mapped_column(String(256))
    ticker: Mapped[str | None] = mapped_column(String(32), index=True)
    sector: Mapped[str | None] = mapped_column(String(64), index=True)
    title: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    external_id: Mapped[str] = mapped_column(String(128), unique=True)
    payload: Mapped[dict | None] = mapped_column(JSON)


class TradeReconciliation(Base):
    """Cross-source data-quality checks between primary parsers and comparison feeds."""

    __tablename__ = "trade_reconciliation"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)  # missing_primary | missing_comparison | mismatch
    primary_trade_id: Mapped[int | None] = mapped_column(ForeignKey("trades.id"), index=True)
    comparison_source: Mapped[str | None] = mapped_column(String(32), index=True)
    comparison_trade_id: Mapped[int | None] = mapped_column(ForeignKey("trades.id"), index=True)
    severity: Mapped[int] = mapped_column(Integer, default=1)
    confidence: Mapped[float | None] = mapped_column(Numeric)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)  # open | resolved | ignored
    resolution_note: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    detail: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)

    __table_args__ = (
        UniqueConstraint("kind", "primary_trade_id", "comparison_source", "comparison_trade_id", name="uq_recon_issue"),
    )


class StrategyRun(Base):
    """Cached backtest of a 'follow-strategy' portfolio (equity curve + metrics vs benchmarks)."""

    __tablename__ = "strategy_runs"

    strategy_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    label: Mapped[str | None] = mapped_column(String(128))
    params: Mapped[dict | None] = mapped_column(JSON)
    equity_curve: Mapped[list | None] = mapped_column(JSON)  # [[date, value, spy, nanc?], ...]
    holdings: Mapped[list | None] = mapped_column(JSON)       # current smart-money basket
    total_return: Mapped[float | None] = mapped_column(Numeric)
    cagr: Mapped[float | None] = mapped_column(Numeric)
    max_drawdown: Mapped[float | None] = mapped_column(Numeric)
    excess_vs_spy: Mapped[float | None] = mapped_column(Numeric)
    n_positions: Mapped[int | None] = mapped_column(Integer)
    generated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class Holding(Base):
    """User paper-portfolio holding (single user behind SSO)."""

    __tablename__ = "holdings"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(32), index=True)
    shares: Mapped[float | None] = mapped_column(Numeric)
    cost_basis: Mapped[float | None] = mapped_column(Numeric)
    note: Mapped[str | None] = mapped_column(Text)
    added_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)

    __table_args__ = (UniqueConstraint("ticker", name="uq_holding_ticker"),)


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
