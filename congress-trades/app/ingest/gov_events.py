"""SEC EDGAR near-real-time corporate filings (8-K material events), joined to tracked tickers
via the company CIK (from ticker_meta). Powers a 'corp_event_after_trade' corroboration signal:
a member bought T, then T filed an 8-K shortly after. Informational/timing, not causation."""
import datetime as dt
import re
import xml.etree.ElementTree as ET

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import load_config
from app.db import SessionLocal, init_db
from app.models import GovEvent, TickerMeta

from . import common

GETCURRENT = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type={form}&company=&dateb=&owner=include&count=100&output=atom"
SEC_UA = "whitehouse-rke2 congress-trades robert@whitematter.tech"
_NS = {"a": "http://www.w3.org/2005/Atom"}
_CIK = re.compile(r"\((\d{4,10})\)")
_ACC = re.compile(r"accession[- ]?number=([\d-]+)")


def run():
    cfg = load_config()
    init_db()
    sess = common.make_session(cfg)
    sess.headers.update({"User-Agent": SEC_UA})
    db = SessionLocal()
    n = 0
    try:
        # CIK -> ticker for tracked companies (cik stored zero-padded to 10)
        cik_to_ticker = {}
        for tk, cik in db.execute(select(TickerMeta.ticker, TickerMeta.cik).where(TickerMeta.cik.isnot(None))).all():
            cik_to_ticker[str(int(cik))] = tk  # normalize leading zeros

        for form in ("8-K",):
            try:
                r = sess.get(GETCURRENT.format(form=form), timeout=30)
                if r.status_code != 200:
                    continue
                root = ET.fromstring(r.text)
                for e in root.findall("a:entry", _NS):
                    title = (e.findtext("a:title", default="", namespaces=_NS) or "").strip()
                    updated = e.findtext("a:updated", default="", namespaces=_NS)
                    link_el = e.find("a:link", _NS)
                    url = link_el.get("href") if link_el is not None else None
                    eid = e.findtext("a:id", default="", namespaces=_NS) or ""
                    accm = _ACC.search(eid) or _ACC.search(url or "")
                    accession = accm.group(1) if accm else eid[-25:]
                    cikm = _CIK.search(title)
                    if not cikm:
                        continue
                    ticker = cik_to_ticker.get(str(int(cikm.group(1))))
                    if not ticker:
                        continue  # only store events for companies we track
                    try:
                        filed = dt.datetime.fromisoformat(updated) if updated else None
                    except ValueError:
                        filed = None
                    db.execute(
                        pg_insert(GovEvent)
                        .values(source="edgar", form=form, cik=cikm.group(1), ticker=ticker,
                                title=title[:300], url=url, filed_at=filed, accession=accession)
                        .on_conflict_do_nothing(index_elements=["accession"])
                    )
                    n += 1
                db.commit()
            except Exception as e:  # noqa: BLE001
                db.rollback()
                print(f"gov_events: {form} failed: {e}")
        common.record_run(db, "gov_events", rows_upserted=n, success=True)
        print(f"gov_events: stored {n} matched filings")
    except Exception as e:  # noqa: BLE001
        common.record_run(db, "gov_events", success=False, note=str(e))
        raise
    finally:
        db.close()


if __name__ == "__main__":
    run()
