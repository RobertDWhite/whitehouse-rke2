"""Custom Prometheus collector exposing ingest freshness for a watchdog alert.

Scraped via the existing /metrics mount. `congress_ingest_age_seconds{source}` = now -
last_success; alert when it exceeds a threshold (e.g. House > 24h on a weekday = scraper
broke). Queried lazily on each scrape so it reflects live DB state."""
import datetime as dt

from prometheus_client.core import REGISTRY, GaugeMetricFamily
from sqlalchemy import select

from .db import SessionLocal
from .models import IngestState, Trade


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
            finally:
                db.close()
        except Exception:  # noqa: BLE001
            pass
        yield age
        yield rows
        yield data_lag


def register():
    try:
        REGISTRY.register(IngestCollector())
    except ValueError:
        pass  # already registered
