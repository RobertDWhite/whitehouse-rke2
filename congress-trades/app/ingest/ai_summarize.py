"""Generate grounded AI summaries of recent congressional trading activity via the
in-cluster Ollama router (qwen2.5:14b by default). Runs as a CronJob, writes ai_summaries
rows the API serves. The model only ever sees a compact aggregated block built from the DB,
and every observation it returns is re-grounded server-side against that data (anything
referencing a ticker/member not in the window is dropped) to prevent hallucination."""
import datetime as dt
import hashlib
import json

import requests
from sqlalchemy import and_, case, func, select

from app.config import load_config
from app.db import SessionLocal, init_db
from app.models import AiSummary, Member, Trade

from . import common

DISCLAIMER = (
    "AI-generated summary of publicly disclosed congressional trades (STOCK Act). "
    "Not financial advice. Disclosed trades are legal. Amounts are reported ranges. "
    "May contain errors — verify against the source filings."
)

SYSTEM = """You are a neutral data analyst for a public dataset of US congressional stock-trade
disclosures (STOCK Act filings). Summarize ONLY what the provided aggregated DATA shows.
Do NOT give financial advice, predict prices, allege illegal activity, or infer motive.
Disclosed trades are legal and routine. Report patterns factually.

Rules:
- State only facts present in DATA. Never invent members, tickers, amounts, committees, or sectors.
- If DATA is sparse, say so. Do not speculate.
- Amounts are disclosed as ranges; never state a precise dollar figure.
- LEAD with the most actionable patterns when the DATA shows them:
  * "hot"/most-purchased tickers (TOP_PURCHASED) and most-sold tickers (TOP_SOLD)
  * coordinated buying — multiple members bought the same ticker (CLUSTER_BUYS)
  * "multiple dumps" — multiple members SOLD the same ticker (CLUSTER_DUMPS)
  * net accumulation vs distribution (NET_PRESSURE: more buys than sells, or vice-versa)
- Output STRICT JSON only (no markdown fences), matching:
{"summary_md":"<3-5 sentence markdown overview leading with the hottest buys/sells and any cluster buying or dumping>",
 "observations":[{"text":"<one factual pattern, <=140 chars>","tickers":["..."],"members":["..."]}]}
Produce 4-7 observations. Prioritize: most-bought, most-sold, cluster buys, cluster dumps, biggest single trades."""

_MID = (func.coalesce(Trade.amount_min, 0) + func.coalesce(Trade.amount_max, Trade.amount_min, 0)) / 2.0


def _ticker_agg(db, where, ttype, limit=8):
    """Top tickers for a transaction type: (ticker, trade_count, distinct_members, volume)."""
    return db.execute(
        select(
            Trade.ticker,
            func.count().label("n"),
            func.count(func.distinct(Trade.member_id)).label("members"),
            func.coalesce(func.sum(_MID), 0).label("vol"),
        )
        .where(and_(where, Trade.ticker.isnot(None), Trade.transaction_type == ttype))
        .group_by(Trade.ticker)
        .order_by(func.count().desc(), func.coalesce(func.sum(_MID), 0).desc())
        .limit(limit)
    ).all()


def _money(n):
    n = float(n or 0)
    if n >= 1e9:
        return f"${n/1e9:.1f}B"
    if n >= 1e6:
        return f"${n/1e6:.1f}M"
    if n >= 1e3:
        return f"${n/1e3:.0f}K"
    return f"${n:.0f}"


def aggregate(db, window_days, member_id=None):
    since = dt.date.today() - dt.timedelta(days=window_days)
    base = [Trade.disclosure_date >= since]
    if member_id:
        base.append(Trade.member_id == member_id)
    where = and_(*base)

    total = db.scalar(select(func.count(Trade.id)).where(where)) or 0
    if total == 0:
        return None
    by_type = dict(
        db.execute(select(Trade.transaction_type, func.count()).where(where).group_by(Trade.transaction_type)).all()
    )
    members_n = db.scalar(select(func.count(func.distinct(Trade.member_id))).where(where)) or 0
    tickers_n = db.scalar(select(func.count(func.distinct(Trade.ticker))).where(where)) or 0
    volume = db.scalar(select(func.coalesce(func.sum(_MID), 0)).where(where)) or 0

    lines = [f"WINDOW: last {window_days} days (disclosed since {since.isoformat()})"]
    lines.append(
        f"TOTALS: trades={total} buys={by_type.get('purchase',0)} sells={by_type.get('sale',0)} "
        f"exchanges={by_type.get('exchange',0)} members={members_n} tickers={tickers_n} volume={_money(volume)}"
    )

    if not member_id:
        top = db.execute(
            select(Member.full_name, Member.party, Member.chamber, Member.state, func.count(Trade.id))
            .join(Trade, Trade.member_id == Member.id)
            .where(where)
            .group_by(Member.id)
            .order_by(func.count(Trade.id).desc())
            .limit(8)
        ).all()
        lines.append("TOP_TRADERS:")
        for name, party, chamber, state, c in top:
            lines.append(f"- {name} ({(party or '?')[:1]}, {chamber}, {state}): {c} trades")

        top_buys = _ticker_agg(db, where, "purchase")
        top_sells = _ticker_agg(db, where, "sale")
        if top_buys:
            lines.append("TOP_PURCHASED (most-bought tickers):")
            for tk, n, mc, vol in top_buys:
                lines.append(f"- {tk}: {n} buys by {mc} member(s), {_money(vol)}")
        if top_sells:
            lines.append("TOP_SOLD (most-sold tickers):")
            for tk, n, mc, vol in top_sells:
                lines.append(f"- {tk}: {n} sells by {mc} member(s), {_money(vol)}")

        clusters = db.execute(
            select(Trade.ticker, func.count(func.distinct(Trade.member_id)))
            .where(and_(where, Trade.ticker.isnot(None), Trade.transaction_type == "purchase"))
            .group_by(Trade.ticker)
            .having(func.count(func.distinct(Trade.member_id)) >= 2)
            .order_by(func.count(func.distinct(Trade.member_id)).desc())
            .limit(8)
        ).all()
        if clusters:
            lines.append("CLUSTER_BUYS (>=2 members bought same ticker):")
            for tk, mc in clusters:
                lines.append(f"- {tk}: {mc} members bought")

        dumps = db.execute(
            select(Trade.ticker, func.count(func.distinct(Trade.member_id)))
            .where(and_(where, Trade.ticker.isnot(None), Trade.transaction_type == "sale"))
            .group_by(Trade.ticker)
            .having(func.count(func.distinct(Trade.member_id)) >= 2)
            .order_by(func.count(func.distinct(Trade.member_id)).desc())
            .limit(8)
        ).all()
        if dumps:
            lines.append("CLUSTER_DUMPS (>=2 members SOLD same ticker):")
            for tk, mc in dumps:
                lines.append(f"- {tk}: {mc} members sold")

        # net buy/sell pressure for the most-active tickers (accumulation vs distribution)
        pressure = db.execute(
            select(
                Trade.ticker,
                func.sum(case((Trade.transaction_type == "purchase", 1), else_=0)),
                func.sum(case((Trade.transaction_type == "sale", 1), else_=0)),
            )
            .where(and_(where, Trade.ticker.isnot(None)))
            .group_by(Trade.ticker)
            .order_by(func.count().desc())
            .limit(10)
        ).all()
        if pressure:
            lines.append("NET_PRESSURE (buys vs sells per active ticker):")
            for tk, b, s in pressure:
                tag = "accumulating" if (b or 0) > (s or 0) else "distributing" if (s or 0) > (b or 0) else "mixed"
                lines.append(f"- {tk}: {int(b or 0)} buys / {int(s or 0)} sells ({tag})")

    big = db.execute(
        select(Member.full_name, Trade.ticker, Trade.transaction_type, Trade.amount_range_raw, Trade.transaction_date)
        .join(Member, Member.id == Trade.member_id, isouter=True)
        .where(where)
        .order_by(func.coalesce(Trade.amount_max, Trade.amount_min).desc().nullslast())
        .limit(8)
    ).all()
    lines.append("BIG_TRADES:")
    for name, tk, ttype, amt, td in big:
        lines.append(f"- {name or '?'} {ttype} {tk or '?'} {amt or ''} ({td})")

    top_tk = db.execute(
        select(Trade.ticker, func.count())
        .where(and_(where, Trade.ticker.isnot(None)))
        .group_by(Trade.ticker)
        .order_by(func.count().desc())
        .limit(10)
    ).all()
    if top_tk:
        lines.append("TOP_TICKERS: " + " ".join(f"{tk}({c})" for tk, c in top_tk))

    data_block = "\n".join(lines)
    extra = set()
    if not member_id:
        for r in top_buys + top_sells:
            extra.add(r[0])
        for r in clusters + dumps + pressure:
            extra.add(r[0])
    tickers_in = {tk for tk, _ in top_tk} | extra
    return {
        "data_block": data_block,
        "total": total,
        "tickers": {t.upper() for t in tickers_in if t},
        "data_hash": hashlib.sha256(data_block.encode()).hexdigest(),
    }


def call_llm(cfg, data_block):
    ai = cfg["ai"]
    models = [ai.get("model", "qwen2.5:14b")] + list(ai.get("fallback_models", []))
    payload_base = {
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"DATA:\n{data_block}\n\nGenerate the summary and observations now."},
        ],
        "temperature": ai.get("temperature", 0.2),
        "max_tokens": 800,
    }
    last_err = None
    for model in models:
        try:
            r = requests.post(
                ai["base_url"].rstrip("/") + "/chat/completions",
                json={**payload_base, "model": model},
                timeout=ai.get("request_timeout", 600.0),
            )
            if r.status_code != 200:
                last_err = f"{model}: HTTP {r.status_code} {r.text[:120]}"
                continue
            content = r.json()["choices"][0]["message"]["content"]
            return model, content
        except Exception as e:  # noqa: BLE001
            last_err = f"{model}: {e}"
    raise RuntimeError(f"all models failed: {last_err}")


def parse_and_ground(content, agg, db, window_days, member_id):
    # tolerate code fences / stray text around the JSON
    txt = content.strip()
    if "```" in txt:
        txt = txt.split("```")[1].lstrip("json").strip() if txt.count("```") >= 2 else txt.replace("```", "")
    start, end = txt.find("{"), txt.rfind("}")
    obj = json.loads(txt[start : end + 1]) if start >= 0 else {}
    summary_md = (obj.get("summary_md") or "").strip()

    # member-name resolution within the window
    since = dt.date.today() - dt.timedelta(days=window_days)
    name_to_id = {}
    rows = db.execute(
        select(Member.full_name, Member.id)
        .join(Trade, Trade.member_id == Member.id)
        .where(Trade.disclosure_date >= since)
        .distinct()
    ).all()
    for name, mid in rows:
        name_to_id[name.lower()] = mid

    grounded = []
    for o in obj.get("observations", []) or []:
        text = (o.get("text") or "").strip()
        if not text:
            continue
        tks = [t.upper() for t in (o.get("tickers") or []) if t]
        mbs = [m for m in (o.get("members") or []) if m]
        # drop observations that cite tickers/members not present in the window's data
        bad_ticker = any(t not in agg["tickers"] for t in tks) and tks
        resolved_members = [name_to_id[m.lower()] for m in mbs if m.lower() in name_to_id]
        if bad_ticker:
            continue
        if mbs and not resolved_members:
            continue
        grounded.append({"text": text[:200], "tickers": tks, "members": mbs, "member_ids": resolved_members})
    return summary_md, grounded


def generate(db, cfg, window_days, member_id=None, scope="global"):
    agg = aggregate(db, window_days, member_id)
    if not agg:
        return False
    # skip if unchanged since last run
    latest = db.scalar(
        select(AiSummary)
        .where(and_(AiSummary.scope == scope, AiSummary.member_id == member_id, AiSummary.window_days == window_days))
        .order_by(AiSummary.generated_at.desc())
        .limit(1)
    )
    if latest and latest.data_hash == agg["data_hash"]:
        return False
    model, content = call_llm(cfg, agg["data_block"])
    summary_md, observations = parse_and_ground(content, agg, db, window_days, member_id)
    db.add(
        AiSummary(
            scope=scope,
            member_id=member_id,
            window_days=window_days,
            summary_md=summary_md,
            observations=observations,
            model=model,
            data_hash=agg["data_hash"],
            trade_count=agg["total"],
            generated_at=dt.datetime.now(dt.timezone.utc),
        )
    )
    db.commit()
    return True


def run():
    cfg = load_config()
    init_db()
    ai = cfg.get("ai", {})
    db = SessionLocal()
    made = 0
    try:
        for w in ai.get("windows", [7, 30]):
            try:
                if generate(db, cfg, int(w), scope="global"):
                    made += 1
                    print(f"ai: global {w}d summary generated")
            except Exception as e:  # noqa: BLE001
                print(f"ai: global {w}d failed: {e}")

        # per-member, only members active in the member window
        mw = int(ai.get("member_window_days", 30))
        since = dt.date.today() - dt.timedelta(days=mw)
        active = [
            mid
            for (mid,) in db.execute(
                select(Trade.member_id).where(and_(Trade.member_id.isnot(None), Trade.disclosure_date >= since)).distinct()
            ).all()
        ]
        for mid in active:
            try:
                if generate(db, cfg, mw, member_id=mid, scope="member"):
                    made += 1
            except Exception as e:  # noqa: BLE001
                print(f"ai: member {mid} failed: {e}")
        common.record_run(db, "ai_summary", rows_upserted=made, success=True)
        print(f"ai: generated {made} summaries (active members: {len(active)})")
    except Exception as e:  # noqa: BLE001
        common.record_run(db, "ai_summary", success=False, note=str(e))
        raise
    finally:
        db.close()


if __name__ == "__main__":
    run()
