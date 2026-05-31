"""Data-quality reconciliation between primary parsers and comparison feeds."""
import datetime as dt

from sqlalchemy import and_, delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db import SessionLocal, init_db
from app.models import Filing, Member, Trade, TradeReconciliation

from . import common


def _issue(db, kind, primary_id=None, comparison_source=None, comparison_id=None, severity=1, detail=None):
    db.execute(
        pg_insert(TradeReconciliation)
        .values(
            kind=kind,
            primary_trade_id=primary_id,
            comparison_source=comparison_source,
            comparison_trade_id=comparison_id,
            severity=severity,
            detail=detail or {},
            created_at=dt.datetime.now(dt.timezone.utc),
        )
        .on_conflict_do_update(
            index_elements=["kind", "primary_trade_id", "comparison_source", "comparison_trade_id"],
            set_={"severity": severity, "detail": detail or {}, "created_at": dt.datetime.now(dt.timezone.utc)},
        )
    )


def run():
    init_db()
    db = SessionLocal()
    n = 0
    try:
        cutoff = dt.date.today() - dt.timedelta(days=120)
        # Rebuild the canary set each run; PostgreSQL unique constraints treat NULLs as distinct.
        db.execute(delete(TradeReconciliation))

        primary_sources = ["house_primary", "senate_primary"]

        # Fuzzy match Lambda rows to primary rows. Exact dedup already happened on insert; this catches
        # same trade with small date/amount/name drift before we call it missing.
        for t, m in db.execute(
            select(Trade, Member)
            .join(Member, Member.id == Trade.member_id, isouter=True)
            .where(and_(Trade.source == "lambda", Trade.disclosure_date >= cutoff))
            .limit(500)
        ).all():
            date_low = t.transaction_date - dt.timedelta(days=3) if t.transaction_date else None
            date_high = t.transaction_date + dt.timedelta(days=3) if t.transaction_date else None
            stmt = (
                select(Trade, Member)
                .join(Member, Member.id == Trade.member_id, isouter=True)
                .where(
                    and_(
                        Trade.source.in_(primary_sources),
                        Trade.ticker == t.ticker,
                        Trade.transaction_type == t.transaction_type,
                    )
                )
                .limit(25)
            )
            if t.transaction_date:
                stmt = stmt.where(Trade.transaction_date.between(date_low, date_high))
            if t.member_id:
                stmt = stmt.where(Trade.member_id == t.member_id)
            candidates = db.execute(stmt).all()
            if candidates:
                best, best_member = candidates[0]
                lambda_mid = float(((t.amount_min or 0) + (t.amount_max or t.amount_min or 0)) / 2)
                primary_mid = float(((best.amount_min or 0) + (best.amount_max or best.amount_min or 0)) / 2)
                diff_ratio = abs(lambda_mid - primary_mid) / max(lambda_mid, primary_mid, 1)
                if diff_ratio > 0.25:
                    _issue(
                        db,
                        "amount_mismatch",
                        primary_id=best.id,
                        comparison_source="lambda",
                        comparison_id=t.id,
                        severity=1,
                        detail={
                            "member": m.full_name if m else best_member.full_name if best_member else None,
                            "ticker": t.ticker,
                            "lambda_amount": t.amount_range_raw,
                            "primary_amount": best.amount_range_raw,
                            "lambda_trade_id": t.id,
                            "primary_trade_id": best.id,
                        },
                    )
                    n += 1
                continue

            _issue(
                db,
                "missing_primary",
                comparison_source="lambda",
                comparison_id=t.id,
                severity=2,
                detail={
                    "member": m.full_name if m else None,
                    "ticker": t.ticker,
                    "transaction_date": t.transaction_date.isoformat() if t.transaction_date else None,
                    "disclosure_date": t.disclosure_date.isoformat() if t.disclosure_date else None,
                    "transaction_type": t.transaction_type,
                    "amount_range": t.amount_range_raw,
                },
            )
            n += 1

        # Primary rows with suspiciously sparse identity are worth checking too.
        sparse = db.execute(
            select(Trade, Member)
            .join(Member, Member.id == Trade.member_id, isouter=True)
            .where(
                and_(
                    Trade.source.in_(primary_sources),
                    Trade.disclosure_date >= cutoff,
                    Trade.ticker.is_(None),
                    Trade.asset_name.isnot(None),
                    func.length(Trade.asset_name) > 20,
                )
            )
            .limit(200)
        ).all()
        for t, m in sparse:
            _issue(
                db,
                "missing_ticker",
                primary_id=t.id,
                comparison_source=t.source,
                severity=1,
                detail={
                    "member": m.full_name if m else None,
                    "asset_name": t.asset_name,
                    "transaction_date": t.transaction_date.isoformat() if t.transaction_date else None,
                    "source": t.source,
                },
            )
            n += 1

        # Parser/OCR failures are reconciliation issues because they may hide transactions.
        for f in db.scalars(
            select(Filing).where(Filing.parse_status.in_(["paper", "error"])).limit(500)
        ).all():
            _issue(
                db,
                "unparsed_filing",
                comparison_source=f.source,
                severity=3 if f.parse_status == "error" else 2,
                detail={
                    "doc_id": f.doc_id,
                    "source_url": f.source_url,
                    "status": f.parse_status,
                    "filing_date": f.filing_date.isoformat() if f.filing_date else None,
                },
            )
            n += 1
        db.commit()
        common.record_run(db, "reconciliation", rows_upserted=n, success=True)
        print(f"reconciliation: {n} issues")
    except Exception as e:  # noqa: BLE001
        common.record_run(db, "reconciliation", success=False, note=str(e))
        raise
    finally:
        db.close()


if __name__ == "__main__":
    run()
