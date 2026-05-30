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
from . import taxonomy

CURRENT = "https://unitedstates.github.io/congress-legislators/legislators-current.json"
HISTORICAL = "https://unitedstates.github.io/congress-legislators/legislators-historical.json"
COMMITTEES = "https://unitedstates.github.io/congress-legislators/committees-current.json"
COMMITTEE_MEMBERSHIP = "https://unitedstates.github.io/congress-legislators/committee-membership-current.json"

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
                "bioguide": (leg.get("id") or {}).get("bioguide"),
            }


def run():
    cfg = load_config()
    init_db()
    sess = common.make_session(cfg)

    lookup = {}
    build_lookup(sess, CURRENT, lookup)           # current members win
    build_lookup(sess, HISTORICAL, lookup)        # backfill former members
    print(f"enrich: loaded {len(lookup)} legislator name keys")

    # committee memberships: bioguide -> [committee display names]
    committees_by_bioguide = {}
    try:
        cmts = {c["thomas_id"]: c.get("name") for c in sess.get(COMMITTEES, timeout=60).json()}
        membership = sess.get(COMMITTEE_MEMBERSHIP, timeout=60).json()
        for cid, members in membership.items():
            name = cmts.get(cid) or cmts.get(cid[:4]) or cid
            for mem in members:
                bg = mem.get("bioguide")
                if bg:
                    committees_by_bioguide.setdefault(bg, set()).add(name)
        print(f"enrich: loaded committees for {len(committees_by_bioguide)} members")
    except Exception as e:  # noqa: BLE001
        print(f"enrich: committee fetch failed: {e}")

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
            if info.get("bioguide") and not m.bioguide:
                m.bioguide = info["bioguide"]
            bg = m.bioguide or info.get("bioguide")
            cmts = sorted(committees_by_bioguide.get(bg, [])) if bg else []
            if cmts:
                m.committees = cmts
                m.committee_sectors = taxonomy.committee_sectors(cmts)
            matched += 1
        db.commit()
        total = db.scalar(select(func.count(Member.id)))
        print(f"enrich: matched {matched}/{total} members")
    finally:
        db.close()


if __name__ == "__main__":
    run()
