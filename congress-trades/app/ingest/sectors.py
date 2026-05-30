"""Tag traded tickers with sector/industry from SEC (free, no key; needs a descriptive UA).

company_tickers.json -> ticker->CIK->company; submissions/CIK{10}.json -> SIC -> broad sector
(via taxonomy.sector_from_sic). Stored in ticker_meta. SEC enforces ~10 req/s + a real UA."""
import datetime as dt
import time

from sqlalchemy import distinct, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import load_config
from app.db import SessionLocal, init_db
from app.models import TickerMeta, Trade

from . import common
from . import taxonomy

COMPANY_TICKERS = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_UA = "whitehouse-rke2 congress-trades robert@whitematter.tech"


def run():
    cfg = load_config()
    init_db()
    sess = common.make_session(cfg)
    sess.headers.update({"User-Agent": SEC_UA, "Accept-Encoding": "gzip, deflate"})
    db = SessionLocal()
    n = 0
    try:
        traded = {t for (t,) in db.execute(select(distinct(Trade.ticker)).where(Trade.ticker.isnot(None))).all()}
        # ticker -> CIK + company
        r = sess.get(COMPANY_TICKERS, timeout=60)
        r.raise_for_status()
        by_ticker = {}
        for row in r.json().values():
            by_ticker[str(row["ticker"]).upper()] = (str(row["cik_str"]).zfill(10), row.get("title"))

        # already have sector for these (skip re-fetch unless older than 30 days)
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)
        have = {
            tk
            for (tk, up) in db.execute(select(TickerMeta.ticker, TickerMeta.updated_at)).all()
            if up and up > cutoff
        }

        for tk in sorted(traded):
            if tk in have or tk not in by_ticker:
                continue
            cik, company = by_ticker[tk]
            sic = sector = None
            try:
                sr = sess.get(SUBMISSIONS.format(cik=cik), timeout=30)
                if sr.status_code == 200:
                    j = sr.json()
                    sic = j.get("sic") or None
                    sector = taxonomy.sector_from_sic(sic)
            except Exception as e:  # noqa: BLE001
                print(f"sectors: {tk} submissions failed: {e}")
            db.execute(
                pg_insert(TickerMeta)
                .values(ticker=tk, cik=cik, company=company, sic=str(sic) if sic else None,
                        sector=sector, updated_at=dt.datetime.now(dt.timezone.utc))
                .on_conflict_do_update(
                    index_elements=["ticker"],
                    set_={"cik": cik, "company": company, "sic": str(sic) if sic else None,
                          "sector": sector, "updated_at": dt.datetime.now(dt.timezone.utc)},
                )
            )
            n += 1
            if n % 25 == 0:
                db.commit()
            time.sleep(0.12)
        db.commit()
        common.record_run(db, "sectors", rows_upserted=n, success=True)
        print(f"sectors: tagged {n} tickers")
    except Exception as e:  # noqa: BLE001
        common.record_run(db, "sectors", success=False, note=str(e))
        raise
    finally:
        db.close()


if __name__ == "__main__":
    run()
