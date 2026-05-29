import datetime as dt

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
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
    chamber: Mapped[str | None] = mapped_column(String(16))
    party: Mapped[str | None] = mapped_column(String(32))
    state: Mapped[str | None] = mapped_column(String(8))
    district: Mapped[str | None] = mapped_column(String(8))
    # Estimated net worth from the latest annual Financial Disclosure (asset-range
    # midpoints minus liabilities). Range because disclosures are reported as $ brackets.
    net_worth_min: Mapped[int | None] = mapped_column(Numeric)
    net_worth_max: Mapped[int | None] = mapped_column(Numeric)
    net_worth_year: Mapped[int | None] = mapped_column(Integer)


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
