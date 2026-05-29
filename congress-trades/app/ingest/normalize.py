import datetime as dt
import hashlib
import re

_TITLES = re.compile(r"\b(hon|mr|mrs|ms|dr|jr|sr|ii|iii|iv)\b\.?")
_AMOUNT = re.compile(r"\$\s*([\d,]+)(?:\s*[-–]\s*\$?\s*([\d,]+))?")


def norm_name(name: str) -> str:
    """Normalize a member name so 'Tuberville, Tommy' and 'Tommy Tuberville (Jr.)'
    collapse to the same key. Tokens are sorted so order doesn't matter."""
    if not name:
        return ""
    n = name.lower()
    # drop "(Tuberville, Tommy)" style parentheticals
    n = re.sub(r"\(.*?\)", " ", n)
    n = n.replace(",", " ")
    n = _TITLES.sub(" ", n)
    n = re.sub(r"[^a-z\s]", " ", n)
    tokens = [t for t in n.split() if len(t) > 1]
    return " ".join(sorted(tokens))


def parse_amount(raw):
    """Returns (min, max, cleaned_raw). 'Over $50,000,000' -> (50000000, None, raw)."""
    if not raw:
        return (None, None, None)
    raw = str(raw).strip()
    over = "over" in raw.lower()
    m = _AMOUNT.search(raw.replace("–", "-"))
    if not m:
        return (None, None, raw)
    lo = int(m.group(1).replace(",", ""))
    hi = int(m.group(2).replace(",", "")) if m.group(2) else None
    if over and hi is None:
        return (lo, None, raw)
    return (lo, hi if hi is not None else lo, raw)


def norm_tx_type(s):
    if not s:
        return "other"
    s = str(s).strip().lower()
    if s.startswith("p") or "purchase" in s:
        return "purchase"
    if s.startswith("s") or "sale" in s or "sold" in s:
        return "sale"
    if s.startswith("e") or "exchange" in s:
        return "exchange"
    return "other"


def parse_date(s):
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def dedup_key(chamber, name_norm, tx_date, ticker, amount_raw, tx_type):
    parts = [
        chamber or "",
        name_norm or "",
        str(tx_date or ""),
        (ticker or "").upper(),
        (amount_raw or "").strip(),
        tx_type or "",
    ]
    return hashlib.sha1("|".join(parts).encode()).hexdigest()
