"""Custom Prometheus collector exposing ingest freshness for a watchdog alert.

Scraped via the existing /metrics mount. `congress_ingest_age_seconds{source}` = now -
last_success; alert when it exceeds a threshold (e.g. House > 24h on a weekday = scraper
broke). Queried lazily on each scrape so it reflects live DB state."""
import datetime as dt

from prometheus_client.core import REGISTRY, GaugeMetricFamily
from sqlalchemy import func, select

from .db import SessionLocal
from .models import Filing, IngestState, Trade, TradeReconciliation, TradeSignal


class IngestCollector:
    def collect(self):
        age = GaugeMetricFamily(
            "congress_ingest_age_seconds", "Seconds since last successful run", labels=["source"]
        )
        rows = GaugeMetricFamily(
            "congress_ingest_rows", "Rows upserted on last run", labels=["source"]
        )
        data_lag = GaugeMetricFamily(
            "congress_data_lag_seconds", "Seconds since the most recent disclosure_date in the DB", labels=[]
        )
        trade_count = GaugeMetricFamily(
            "congress_trades_total", "Stored trades by source", labels=["source"]
        )
        filing_status = GaugeMetricFamily(
            "congress_filings_total", "Filings by parse status", labels=["source", "status"]
        )
        reconciliation = GaugeMetricFamily(
            "congress_reconciliation_issues", "Reconciliation issues by kind and status", labels=["kind", "status"]
        )
        signals = GaugeMetricFamily(
            "congress_trade_signals_total", "Trade signals by type and alert state", labels=["signal_type", "alerted"]
        )
        try:
            db = SessionLocal()
            try:
                now = dt.datetime.now(dt.timezone.utc)
                for st in db.scalars(select(IngestState)).all():
                    if st.last_success:
                        age.add_metric([st.source], (now - st.last_success).total_seconds())
                    if st.rows_upserted is not None:
                        rows.add_metric([st.source], st.rows_upserted)
                latest = db.scalar(select(Trade.disclosure_date).order_by(Trade.disclosure_date.desc().nullslast()).limit(1))
                if latest:
                    secs = (dt.date.today() - latest).days * 86400
                    data_lag.add_metric([], secs)
                for src, count in db.execute(select(Trade.source, func.count()).group_by(Trade.source)).all():
                    trade_count.add_metric([src or "unknown"], count)
                for src, status, count in db.execute(select(Filing.source, Filing.parse_status, func.count()).group_by(Filing.source, Filing.parse_status)).all():
                    filing_status.add_metric([src or "unknown", status or "unknown"], count)
                for kind, status, count in db.execute(select(TradeReconciliation.kind, TradeReconciliation.status, func.count()).group_by(TradeReconciliation.kind, TradeReconciliation.status)).all():
                    reconciliation.add_metric([kind or "unknown", status or "open"], count)
                for stype, alerted, count in db.execute(
                    select(TradeSignal.signal_type, TradeSignal.alerted_at.isnot(None), func.count()).group_by(TradeSignal.signal_type, TradeSignal.alerted_at.isnot(None))
                ).all():
                    signals.add_metric([stype or "unknown", "true" if alerted else "false"], count)
            finally:
                db.close()
        except Exception:  # noqa: BLE001
            pass
        yield age
        yield rows
        yield data_lag
        yield trade_count
        yield filing_status
        yield reconciliation
        yield signals


def register():
    try:
        REGISTRY.register(IngestCollector())
    except ValueError:
        pass  # already registered
