"""Enrich members with party / state / district / chamber from the public, free
`unitedstates/congress-legislators` dataset (no API key). Disclosure filings don't
include party, so this is what makes the (R)/(D)/(I) tags reliable.

Matched on (first-token, last-token) of the name to tolerate middle names, nicknames,
titles, and "Last, First" ordering differences."""
import re

from app.config import load_config
from app.db import SessionLocal, init_db
from app.models import Member
from sqlalchemy import func, select

from . import common

CURRENT = "https://unitedstates.github.io/congress-legislators/legislators-current.json"
HISTORICAL = "https://unitedstates.github.io/congress-legislators/legislators-historical.json"

_TITLES = {"hon", "mr", "mrs", "ms", "dr", "jr", "sr", "ii", "iii", "iv", "dds", "md"}


def name_key(full_name: str):
    """(first, last) lowercase-alpha tokens, ignoring titles/suffixes/middles."""
    if not full_name:
        return None
    toks = [re.sub(r"[^a-z]", "", t.lower()) for t in re.sub(r"[,.]", " ", full_name).split()]
    toks = [t for t in toks if t and t not in _TITLES]
    if len(toks) < 2:
        return None
    return (toks[0], toks[-1])


def build_lookup(sess, url, table):
    r = sess.get(url, timeout=120)
    r.raise_for_status()
    for leg in r.json():
        nm = leg.get("name", {})
        terms = leg.get("terms") or []
        if not terms:
            continue
        last_term = terms[-1]
        full = nm.get("official_full") or f"{nm.get('first', '')} {nm.get('last', '')}"
        for key in {
            name_key(full),
            name_key(f"{nm.get('first', '')} {nm.get('last', '')}"),
            name_key(f"{nm.get('nickname', '')} {nm.get('last', '')}") if nm.get("nickname") else None,
        }:
            if not key or key in table:
                continue
            table[key] = {
                "party": last_term.get("party"),
                "state": last_term.get("state"),
                "district": str(last_term["district"]) if last_term.get("district") is not None else None,
                "chamber": {"rep": "house", "sen": "senate"}.get(last_term.get("type")),
            }


def run():
    cfg = load_config()
    init_db()
    sess = common.make_session(cfg)

    lookup = {}
    build_lookup(sess, CURRENT, lookup)           # current members win
    build_lookup(sess, HISTORICAL, lookup)        # backfill former members
    print(f"enrich: loaded {len(lookup)} legislator name keys")

    db = SessionLocal()
    matched = 0
    try:
        for m in db.scalars(select(Member)).all():
            info = lookup.get(name_key(m.full_name))
            if not info:
                continue
            if info["party"]:
                m.party = info["party"]
            if info["state"] and not m.state:
                m.state = info["state"]
            if info["district"] and not m.district:
                m.district = info["district"]
            if info["chamber"] and not m.chamber:
                m.chamber = info["chamber"]
            matched += 1
        db.commit()
        total = db.scalar(select(func.count(Member.id)))
        print(f"enrich: matched {matched}/{total} members")
    finally:
        db.close()


if __name__ == "__main__":
    run()
