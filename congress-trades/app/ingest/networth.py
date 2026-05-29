"""Estimate member net worth from the annual House Financial Disclosure reports.

PTRs (transactions) don't contain net worth — the annual FD report does, in Schedule A
(Assets and Unearned Income) with each asset's value as a $ range, plus a liabilities
schedule. We estimate net worth = sum(asset value-range) - sum(liability range), as a range
(disclosures are bracketed). Annual reports are FilingType O/C/A/D with DocID starting '1',
served from financial-pdfs/{year}/{docid}.pdf (text; OCR fallback for scanned).

This is an ESTIMATE from disclosed ranges, not an exact figure."""
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
from app.models import Member
from sqlalchemy import select

from . import common
from . import normalize as nz

_AMT = re.compile(r"\$\s*([\d,]+)(?:\s*[-–]\s*\$?\s*([\d,]+))?")
ASSET_FILING_TYPES = {"O", "C", "A", "D"}  # reports that carry a Schedule A asset table
# Largest FD value bracket is "$25,000,001-$50,000,000" / "over $50,000,000"; any single
# value above this is a parse error (e.g. mashed digits) — clamp it.
_BRACKET_CAP = 50_000_000
# Total net worth above this is treated as a parse error and discarded.
_TOTAL_SANITY = 3_000_000_000


def extract_text(pdf_bytes):
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(pdf_bytes)
        path = f.name
    try:
        out = subprocess.run(["pdftotext", "-layout", path, "-"], capture_output=True, timeout=120)
        return out.stdout.decode("utf-8", "ignore")
    finally:
        os.unlink(path)


def ocr_text(pdf_bytes, dpi=200):
    from pdf2image import convert_from_bytes
    import pytesseract

    return "\n".join(pytesseract.image_to_string(img) for img in convert_from_bytes(pdf_bytes, dpi=dpi))


def estimate_net_worth(text):
    """Returns (assets_lo, assets_hi, liab_lo, liab_hi) or None if no asset schedule found.
    Within Schedule A each row's FIRST $-range is the 'Value of Asset' (income amounts come
    later on the row); ranges that wrap to the next line are completed."""
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines) if re.search(r"Value of Asset", l, re.I)), None)
    if start is None:
        return None
    a_lo = a_hi = l_lo = l_hi = 0
    mode = "asset"
    for i in range(start + 1, len(lines)):
        l = lines[i]
        if re.search(r"SCHEDULE C|SCHEDULE D|Liabilities", l, re.I):
            mode = "liab"
        elif re.search(r"SCHEDULE [E-J]|Positions|Agreements|Compensation|Gifts|Travel", l, re.I):
            mode = "skip"
        m = _AMT.search(l)
        if not m:
            continue
        lo = int(m.group(1).replace(",", ""))
        hi = int(m.group(2).replace(",", "")) if m.group(2) else None
        if hi is None and l.rstrip().endswith("-") and i + 1 < len(lines):
            m2 = _AMT.search(lines[i + 1])
            if m2:
                hi = int(m2.group(1).replace(",", ""))
        if hi is None:
            hi = lo
        # clamp misparsed/over-cap values to the largest real FD bracket
        lo = min(lo, _BRACKET_CAP)
        hi = min(hi, _BRACKET_CAP)
        if mode == "asset":
            a_lo += lo
            a_hi += hi
        elif mode == "liab":
            l_lo += lo
            l_hi += hi
    return a_lo, a_hi, l_lo, l_hi


def run():
    cfg = load_config()
    init_db()
    sess = common.make_session(cfg)
    hc = cfg["house"]
    years = hc.get("years") or [dt.date.today().year - 1, dt.date.today().year]
    ocr_enabled = bool(cfg.get("ocr", {}).get("enabled"))
    ocr_dpi = int(cfg.get("ocr", {}).get("dpi", 200))

    db = SessionLocal()
    updated = 0
    try:
        # only enrich members we already track (from trades) — never create from FD filings,
        # which would pull in non-member candidates
        by_key = {}
        for mem in db.scalars(select(Member)).all():
            if mem.name_norm:
                by_key[mem.name_norm] = mem
        # process oldest->newest so the most recent year's report wins per member
        for year in sorted(years):
            z = sess.get(hc["zip_url_template"].format(year=year), timeout=180)
            if z.status_code != 200:
                print(f"networth {year}: zip HTTP {z.status_code}")
                continue
            zf = zipfile.ZipFile(io.BytesIO(z.content))
            xml = next((n for n in zf.namelist() if n.lower().endswith(".xml")), None)
            if not xml:
                continue
            root = ET.fromstring(zf.read(xml).decode("utf-8-sig"))
            for mem in root.findall(".//Member"):
                if (mem.findtext("FilingType") or "").strip() not in ASSET_FILING_TYPES:
                    continue
                doc = (mem.findtext("DocID") or "").strip()
                if not doc.startswith("1"):
                    continue
                first = (mem.findtext("First") or "").strip()
                last = (mem.findtext("Last") or "").strip()
                full = " ".join(p for p in (first, last, (mem.findtext("Suffix") or "").strip()) if p)
                member = by_key.get(nz.norm_name(full))
                if not member:
                    continue  # not a tracked member — skip (don't create candidates)
                url = f"https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}/{doc}.pdf"
                try:
                    pr = sess.get(url, timeout=120)
                    if pr.status_code != 200:
                        continue
                    text = extract_text(pr.content)
                    if len(text.strip()) < 800 and ocr_enabled:
                        text = ocr_text(pr.content, ocr_dpi)
                except Exception as e:  # noqa: BLE001
                    print(f"networth fetch {doc}: {e}")
                    continue
                est = estimate_net_worth(text)
                if not est:
                    continue
                a_lo, a_hi, l_lo, l_hi = est
                if a_lo == 0 and a_hi == 0:
                    continue
                if a_hi > _TOTAL_SANITY:
                    print(f"networth: discard {full} {year} — assets ${a_hi:,} exceed sanity cap")
                    continue
                # newest year wins (we iterate years ascending)
                member.net_worth_min = a_lo - l_hi
                member.net_worth_max = a_hi - l_lo
                member.net_worth_year = year
                updated += 1
                if updated % 25 == 0:
                    db.commit()
                    print(f"networth {year}: updated {updated}")
            db.commit()
            print(f"networth {year}: done")
        print(f"networth: total members updated {updated}")
    finally:
        db.close()


if __name__ == "__main__":
    run()
