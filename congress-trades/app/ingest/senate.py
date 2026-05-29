"""Daily Senate primary-source ingest via the eFD search.

There is no bulk export. We accept the prohibition-of-use agreement (sets a Django session +
CSRF cookie), paginate the report/data JSON endpoint for PTRs, then parse each electronic
report's HTML transaction table. Paper (scanned) filings are recorded for later OCR.
Self-throttled; re-agrees if the session cookie expires. Modeled on neelsomani/senator-filings."""
import datetime as dt
import re
import time

from bs4 import BeautifulSoup
from app.config import load_config
from app.db import SessionLocal, init_db
from app.models import Filing

from . import common
from . import normalize as nz

HOME = "/search/home/"
DATA = "/search/report/data/"
_CSRF = re.compile(r'name="csrfmiddlewaretoken"\s+value="([^"]+)"')
_HREF = re.compile(r'href="([^"]+)"')
_STATE = re.compile(r"\b([A-Z]{2})\b")


def _csrf_token(html, sess):
    m = _CSRF.search(html or "")
    if m:
        return m.group(1)
    return sess.cookies.get("csrftoken") or sess.cookies.get("csrf")


def agree(sess, base):
    r = sess.get(base + HOME, timeout=30)
    token = _csrf_token(r.text, sess)
    sess.post(
        base + HOME,
        data={"csrfmiddlewaretoken": token, "prohibition_agreement": "1"},
        headers={"Referer": base + HOME},
        timeout=30,
    )
    return sess.cookies.get("csrftoken") or token


def parse_office_state(office):
    # e.g. "Senator, Tommy Tuberville (Tuberville, Tommy)" — no state; state often absent here.
    if not office:
        return None
    m = _STATE.search(office)
    return m.group(1) if m else None


def parse_ptr_html(sess, base, href, throttle):
    """Fetch an electronic PTR page and extract its 9-column transaction rows."""
    url = base + href if href.startswith("/") else href
    r = sess.get(url, timeout=60)
    if "prohibition" in r.text.lower() and "agreement" in r.text.lower():
        # cookie expired -> re-agree and retry once
        agree(sess, base)
        time.sleep(throttle)
        r = sess.get(url, timeout=60)
    soup = BeautifulSoup(r.text, "lxml")
    rows = []
    for tr in soup.select("table tbody tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(cells) < 8:
            continue
        # 0:#  1:tx_date  2:owner  3:ticker  4:asset  5:asset_type  6:tx_type  7:amount  8:comment
        tx_date, owner, ticker, asset, asset_type, tx_type, amount = (
            cells[1],
            cells[2],
            cells[3],
            cells[4],
            cells[5],
            cells[6],
            cells[7],
        )
        comment = cells[8] if len(cells) > 8 else None
        ticker = None if ticker in ("--", "") else ticker
        lo, hi, raw = nz.parse_amount(amount)
        rows.append(
            {
                "transaction_date": nz.parse_date(tx_date),
                "owner": owner or None,
                "ticker": ticker,
                "asset_name": asset or None,
                "asset_type": asset_type or None,
                "transaction_type": nz.norm_tx_type(tx_type),
                "amount_min": lo,
                "amount_max": hi,
                "amount_range_raw": raw or amount,
                "comment": comment or None,
            }
        )
    return rows, r.text


def run():
    cfg = load_config()
    init_db()
    sc = cfg["senate"]
    base = sc["base"]
    throttle = sc.get("throttle_seconds", 2)
    page_size = sc.get("page_size", 100)
    report_types = str(sc.get("report_types", [11])).replace(" ", "")

    sess = common.make_session(cfg)
    token = agree(sess, base)

    # incremental: results are ordered newest-first; once we've seen this many consecutive
    # already-parsed filings we can stop (0 disables = full backfill scan).
    stop_after_known = int(sc.get("stop_after_known", 0))
    consecutive_known = 0

    db = SessionLocal()
    processed = 0
    stop = False
    try:
        start = 0
        while not stop:
            payload = {
                "start": str(start),
                "length": str(page_size),
                "report_types": report_types,
                "filer_types": "[]",
                "submitted_start_date": sc.get("start_date", ""),
                "submitted_end_date": "",
                "candidate_state": "",
                "senator_state": "",
                "office_id": "",
                "first_name": "",
                "last_name": "",
                # order by "date received" (col 4) descending → newest first
                "order[0][column]": "4",
                "order[0][dir]": "desc",
                "csrfmiddlewaretoken": token,
            }
            r = sess.post(
                base + DATA,
                data=payload,
                headers={"Referer": base + "/search/"},
                timeout=60,
            )
            if r.status_code != 200:
                print(f"senate: report/data HTTP {r.status_code}")
                break
            data = r.json().get("data", [])
            if not data:
                break

            for row in data:
                first, last, office, link_html, date_recv = (
                    row[0],
                    row[1],
                    row[2],
                    row[3],
                    row[4],
                )
                hm = _HREF.search(link_html or "")
                if not hm:
                    continue
                href = hm.group(1)
                doc_id = href.strip("/").split("/")[-1]
                is_paper = "/paper/" in href

                existing = common.get_filing(db, "senate", doc_id)
                if existing and existing.parse_status in ("parsed", "ocr", "paper"):
                    consecutive_known += 1
                    if stop_after_known and consecutive_known >= stop_after_known:
                        print(f"senate: {consecutive_known} consecutive known filings — stopping (incremental)")
                        stop = True
                        break
                    continue
                consecutive_known = 0

                full = f"{first} {last}".strip()
                state = parse_office_state(office)
                member = common.get_or_create_member(db, full, chamber="senate", state=state)
                filing_date = nz.parse_date(date_recv)

                f = existing or Filing(source="senate", doc_id=doc_id)
                if not existing:
                    db.add(f)
                f.chamber = "senate"
                f.member_id = member.id if member else None
                f.filing_type = "ptr"
                f.filing_date = filing_date
                f.source_url = base + href if href.startswith("/") else href
                f.fetched_at = dt.datetime.now(dt.timezone.utc)

                if is_paper:
                    # scanned PDF — flag for the OCR path, no transactions extracted here
                    f.parse_status = "paper"
                    db.flush()
                    processed += 1
                    continue

                time.sleep(throttle)
                try:
                    txns, raw = parse_ptr_html(sess, base, href, throttle)
                    f.parse_status = "parsed"
                    f.raw_text = raw[:200000] if raw else None
                    db.flush()
                    for txn in txns:
                        common.upsert_trade(
                            db,
                            source="senate_primary",
                            member=member,
                            chamber="senate",
                            filing_id=f.id,
                            transaction_date=txn["transaction_date"],
                            disclosure_date=filing_date,
                            owner=txn["owner"],
                            ticker=txn["ticker"],
                            asset_name=txn["asset_name"],
                            asset_type=txn["asset_type"],
                            transaction_type=txn["transaction_type"],
                            amount_min=txn["amount_min"],
                            amount_max=txn["amount_max"],
                            amount_range_raw=txn["amount_range_raw"],
                            comment=txn["comment"],
                        )
                except Exception as e:  # noqa: BLE001
                    f.parse_status = "error"
                    db.flush()
                    print(f"senate parse fail {doc_id}: {e}")

                processed += 1
                if processed % 20 == 0:
                    db.commit()
                    print(f"senate: processed {processed}")

            start += page_size
            time.sleep(throttle)

        db.commit()
        common.record_run(db, "senate", rows_upserted=processed, success=True)
        print(f"senate: done, {processed} filings processed")
    except Exception as e:  # noqa: BLE001
        common.record_run(db, "senate", success=False, note=str(e))
        print(f"senate: FAILED {e}")
    finally:
        db.close()


if __name__ == "__main__":
    run()
