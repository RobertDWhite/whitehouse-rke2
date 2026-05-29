"""Daily House primary-source ingest.

Downloads the daily-rebuilt bulk index ZIP, filters PTRs (FilingType == 'P'), fetches each
PTR PDF, extracts text (pdftotext; OCR fallback for scanned filings whose DocID starts '8'),
parses transaction rows, and upserts them as the authoritative 'house_primary' source."""
import datetime as dt
import io
import os
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile

from app.config import load_config
from app.db import SessionLocal, init_db
from app.models import Filing

from . import common
from . import normalize as nz

_DATE = re.compile(r"\b(\d{1,2}/\d{1,2}/\d{4})\b")
_TICKER = re.compile(r"\(([A-Z][A-Z0-9.\-]{0,5})\)(?:\s*\[([A-Z]{1,3})\])?")
_AMOUNT = re.compile(r"\$[\d,]+(?:\s*[-–]\s*\$?[\d,]+)?\+?|Over\s+\$[\d,]+", re.IGNORECASE)
_TYPE = re.compile(r"(?<![A-Za-z])(P|S \(partial\)|S|E)(?![A-Za-z])")
_OWNER = re.compile(r"^(SP|DC|JT)\b")


def default_years():
    y = dt.date.today().year
    return [y - 1, y]


def extract_text(pdf_bytes):
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(pdf_bytes)
        path = f.name
    try:
        out = subprocess.run(
            ["pdftotext", "-layout", path, "-"],
            capture_output=True,
            timeout=120,
        )
        return out.stdout.decode("utf-8", "ignore")
    finally:
        os.unlink(path)


def ocr_text(pdf_bytes, dpi=200):
    from pdf2image import convert_from_bytes
    import pytesseract

    images = convert_from_bytes(pdf_bytes, dpi=dpi)
    return "\n".join(pytesseract.image_to_string(img) for img in images)


def parse_transactions(text):
    """Heuristic line parser for House PTR text. A line is treated as a transaction when it
    carries an amount range, at least one date, and (usually) a ticker. raw_text is also
    persisted on the filing so parsing can be refined against real samples later."""
    rows = []
    for line in text.splitlines():
        amt_m = _AMOUNT.search(line)
        if not amt_m:
            continue
        dates = _DATE.findall(line)
        if not dates:
            continue
        tk = _TICKER.search(line)
        ticker = tk.group(1) if tk else None
        asset_type = tk.group(2) if (tk and tk.lastindex and tk.group(2)) else None

        asset_name = (line[: tk.start()] if tk else line).strip()
        owner = None
        om = _OWNER.match(asset_name)
        if om:
            owner = om.group(1)
            asset_name = asset_name[om.end():].strip()

        ttype = None
        tail = line[tk.end():] if tk else line
        tm = _TYPE.search(tail)
        if tm:
            ttype = tm.group(1)

        lo, hi, raw = nz.parse_amount(amt_m.group(0))
        rows.append(
            {
                "owner": owner,
                "ticker": ticker,
                "asset_name": asset_name or None,
                "asset_type": asset_type,
                "transaction_type": nz.norm_tx_type(ttype),
                "transaction_date": nz.parse_date(dates[0]),
                "disclosure_date": nz.parse_date(dates[1]) if len(dates) > 1 else None,
                "amount_min": lo,
                "amount_max": hi,
                "amount_range_raw": raw,
            }
        )
    return rows


def ingest_year(db, sess, hc, year, cfg):
    url = hc["zip_url_template"].format(year=year)
    r = sess.get(url, timeout=180)
    if r.status_code != 200:
        print(f"house {year}: zip HTTP {r.status_code}")
        return

    zf = zipfile.ZipFile(io.BytesIO(r.content))
    xml_name = next((n for n in zf.namelist() if n.lower().endswith(".xml")), None)
    if not xml_name:
        print(f"house {year}: no XML index in zip")
        return
    root = ET.fromstring(zf.read(xml_name).decode("utf-8-sig"))

    ocr_enabled = bool(cfg.get("ocr", {}).get("enabled"))
    ocr_dpi = int(cfg.get("ocr", {}).get("dpi", 200))
    processed = 0

    for mem in root.findall(".//Member"):
        if (mem.findtext("FilingType") or "").strip() != "P":
            continue
        doc_id = (mem.findtext("DocID") or "").strip()
        if not doc_id:
            continue

        existing = common.get_filing(db, "house", doc_id)
        if existing and existing.parse_status in ("parsed", "ocr", "paper"):
            continue

        first = (mem.findtext("First") or "").strip()
        last = (mem.findtext("Last") or "").strip()
        suffix = (mem.findtext("Suffix") or "").strip()
        full = " ".join(p for p in (first, last, suffix) if p)
        statedst = (mem.findtext("StateDst") or "").strip()
        state = statedst[:2] or None
        district = statedst[2:] or None
        filing_date = nz.parse_date(mem.findtext("FilingDate"))
        member = common.get_or_create_member(db, full, chamber="house", state=state, district=district)

        pdf_url = hc["ptr_pdf_template"].format(year=year, doc_id=doc_id)
        status = "pending"
        text = ""
        try:
            pr = sess.get(pdf_url, timeout=120)
            if pr.status_code != 200:
                status = "error"
            else:
                text = extract_text(pr.content)
                if len(text.strip()) < 30:
                    if ocr_enabled:
                        try:
                            text = ocr_text(pr.content, ocr_dpi)
                            status = "ocr"
                        except Exception as e:  # noqa: BLE001
                            status = "paper"
                            print(f"house ocr fail {doc_id}: {e}")
                    else:
                        status = "paper"
                else:
                    status = "parsed"
        except Exception as e:  # noqa: BLE001
            status = "error"
            print(f"house fetch fail {doc_id}: {e}")

        f = existing or Filing(source="house", doc_id=doc_id)
        if not existing:
            db.add(f)
        f.chamber = "house"
        f.member_id = member.id if member else None
        f.filing_type = "P"
        f.filing_date = filing_date
        f.source_url = pdf_url
        f.parse_status = status
        f.raw_text = text[:200000] if text else None
        f.fetched_at = dt.datetime.now(dt.timezone.utc)
        db.flush()

        if status in ("parsed", "ocr"):
            for txn in parse_transactions(text):
                common.upsert_trade(
                    db,
                    source="house_primary",
                    member=member,
                    chamber="house",
                    filing_id=f.id,
                    transaction_date=txn["transaction_date"],
                    disclosure_date=txn["disclosure_date"] or filing_date,
                    owner=txn["owner"],
                    ticker=txn["ticker"],
                    asset_name=txn["asset_name"],
                    asset_type=txn["asset_type"],
                    transaction_type=txn["transaction_type"],
                    amount_min=txn["amount_min"],
                    amount_max=txn["amount_max"],
                    amount_range_raw=txn["amount_range_raw"],
                )

        processed += 1
        if processed % 25 == 0:
            db.commit()
            print(f"house {year}: processed {processed}")

    db.commit()
    print(f"house {year}: done, {processed} new/updated filings")


def run():
    cfg = load_config()
    init_db()
    sess = common.make_session(cfg)
    hc = cfg["house"]
    years = hc.get("years") or default_years()
    db = SessionLocal()
    try:
        for year in years:
            ingest_year(db, sess, hc, year, cfg)
    finally:
        db.close()


if __name__ == "__main__":
    run()
