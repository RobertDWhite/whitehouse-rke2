"""Score trades into the trade_signals table and push notable new ones to ntfy.

All signals compute from data already stored. Designed to run right after each ingest
(or on its own short tick). Idempotent: signals upsert on (trade_id, signal_type), and
each fired alert marks `alerted_at` so re-runs don't re-notify."""
import datetime as dt

import requests
from sqlalchemy import and_, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import load_config
from app.db import SessionLocal, init_db
from app.models import Member, TickerMeta, Trade, TradeSignal, Watchlist

# signal types that should trigger ntfy alerts (conviction is a display score, not an alert)
ALERT_TYPES = ["cluster_buy", "large", "options", "late_disclosure", "anomaly", "conflict"]

from . import common


def _midexpr():
    return (func.coalesce(Trade.amount_min, 0) + func.coalesce(Trade.amount_max, Trade.amount_min, 0)) / 2.0


def upsert_signal(db, trade_id, stype, score, detail):
    stmt = pg_insert(TradeSignal).values(
        trade_id=trade_id, signal_type=stype, score=score, detail=detail,
        created_at=dt.datetime.now(dt.timezone.utc),
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["trade_id", "signal_type"],
        set_={"score": stmt.excluded.score, "detail": stmt.excluded.detail},
    )
    db.execute(stmt)


def compute(db, cfg):
    sc = cfg.get("signals", {})
    window = int(sc.get("window_days", 14))
    large = int(sc.get("large_usd", 100000))
    cluster_min = int(sc.get("cluster_min_members", 2))
    base_mult = float(sc.get("baseline_multiple", 5))
    lag_days = int(sc.get("lag_outlier_days", 40))
    since = dt.date.today() - dt.timedelta(days=window)
    n = 0

    # 1) large notional (tiered)
    for t in db.scalars(select(Trade).where(func.coalesce(Trade.amount_min, 0) >= large)).all():
        amt = float(t.amount_min or 0)
        score = 3 if amt >= 1_000_000 else 2 if amt >= 250_000 else 1
        upsert_signal(db, t.id, "large", score, {"amount_min": amt})
        n += 1

    # 2) options / derivatives
    opt = db.scalars(
        select(Trade).where(
            Trade.asset_type.ilike("%op%")
            | Trade.asset_name.op("~*")("option|call|put|warrant|strike|expir")
        )
    ).all()
    for t in opt:
        upsert_signal(db, t.id, "options", 3, {"asset_type": t.asset_type})
        n += 1

    # 3) disclosure-lag outlier
    lag_rows = db.scalars(
        select(Trade).where(
            and_(
                Trade.transaction_date.isnot(None),
                Trade.disclosure_date.isnot(None),
                (Trade.disclosure_date - Trade.transaction_date) >= lag_days,
            )
        )
    ).all()
    for t in lag_rows:
        lag = (t.disclosure_date - t.transaction_date).days
        upsert_signal(db, t.id, "late_disclosure", 2, {"lag_days": lag})
        n += 1

    # 4) cluster buying — >= N distinct members bought same ticker in the window
    cluster = db.execute(
        select(Trade.ticker, func.count(func.distinct(Trade.member_id)).label("members"))
        .where(
            and_(
                Trade.ticker.isnot(None),
                Trade.transaction_type == "purchase",
                Trade.disclosure_date >= since,
            )
        )
        .group_by(Trade.ticker)
        .having(func.count(func.distinct(Trade.member_id)) >= cluster_min)
    ).all()
    for ticker, members in cluster:
        score = 3 if members >= 3 else 2
        trades = db.scalars(
            select(Trade).where(
                and_(Trade.ticker == ticker, Trade.transaction_type == "purchase", Trade.disclosure_date >= since)
            )
        ).all()
        for t in trades:
            upsert_signal(db, t.id, "cluster_buy", score, {"ticker": ticker, "members": int(members)})
            n += 1

    # 4b) cluster selling ("multiple dumps") — >= N distinct members sold same ticker in window
    cluster_s = db.execute(
        select(Trade.ticker, func.count(func.distinct(Trade.member_id)).label("members"))
        .where(and_(Trade.ticker.isnot(None), Trade.transaction_type == "sale", Trade.disclosure_date >= since))
        .group_by(Trade.ticker)
        .having(func.count(func.distinct(Trade.member_id)) >= cluster_min)
    ).all()
    for ticker, members in cluster_s:
        score = 3 if members >= 3 else 2
        for t in db.scalars(
            select(Trade).where(
                and_(Trade.ticker == ticker, Trade.transaction_type == "sale", Trade.disclosure_date >= since)
            )
        ).all():
            upsert_signal(db, t.id, "cluster_sell", score, {"ticker": ticker, "members": int(members)})
            n += 1

    # 5) member-baseline anomaly — trade >= base_mult x that member's median amount_min
    medians = db.execute(
        select(
            Trade.member_id,
            func.percentile_cont(0.5).within_group(func.coalesce(Trade.amount_min, 0)).label("med"),
        )
        .where(Trade.member_id.isnot(None))
        .group_by(Trade.member_id)
    ).all()
    med_by_member = {mid: float(med or 0) for mid, med in medians}
    recent = db.scalars(select(Trade).where(Trade.disclosure_date >= since)).all()
    for t in recent:
        med = med_by_member.get(t.member_id, 0)
        amt = float(t.amount_min or 0)
        if med > 0 and amt >= base_mult * med and amt >= 50_000:
            upsert_signal(db, t.id, "anomaly", 2, {"amount_min": amt, "member_median": med})
            n += 1

    # 6) conflict-of-interest — member trades a ticker in a sector their committee oversees
    for tid, csectors, sector, tk in db.execute(
        select(Trade.id, Member.committee_sectors, TickerMeta.sector, Trade.ticker)
        .join(Member, Member.id == Trade.member_id)
        .join(TickerMeta, TickerMeta.ticker == Trade.ticker)
        .where(and_(Member.committee_sectors.isnot(None), TickerMeta.sector.isnot(None)))
    ).all():
        if csectors and sector in csectors:
            upsert_signal(db, tid, "conflict", 3, {"sector": sector, "ticker": tk})
            n += 1

    db.commit()
    compute_conviction(db)
    return n


def compute_conviction(db):
    """Additive 0-100 'disclosed conviction' per PURCHASE (transparent, capped, never multiplied).
    Components: trade size, size vs member net worth, cluster size, options, anomaly, conflict,
    and the member's historical follower-excess track record. Stored as signal_type='conviction'
    with the component breakdown in detail so the UI can explain the number."""
    # presence/detail maps for the contributing signals
    sig = {}
    for tid, stype, detail in db.execute(
        select(TradeSignal.trade_id, TradeSignal.signal_type, TradeSignal.detail).where(
            TradeSignal.signal_type.in_(["cluster_buy", "options", "anomaly", "conflict"])
        )
    ).all():
        sig.setdefault(tid, {})[stype] = detail or {}

    # member net worth + track record (avg follower excess over their purchases)
    nets, track = {}, {}
    for mid, nw in db.execute(select(Member.id, Member.net_worth_max)).all():
        nets[mid] = float(nw) if nw is not None else None
    for mid, exc in db.execute(
        select(Trade.member_id, func.avg(Trade.return_pct - Trade.bench_return_pct))
        .where(and_(Trade.transaction_type == "purchase", Trade.return_pct.isnot(None)))
        .group_by(Trade.member_id)
    ).all():
        track[mid] = float(exc) if exc is not None else None

    for t in db.scalars(select(Trade).where(Trade.transaction_type == "purchase")).all():
        amt = float(t.amount_min or 0)
        size = 25 if amt >= 1_000_000 else 18 if amt >= 250_000 else 12 if amt >= 100_000 else 6 if amt >= 50_000 else 3 if amt >= 15_000 else 0
        nw = nets.get(t.member_id)
        rel = (amt / nw) if (nw and nw > 0) else 0
        size_nw = 15 if rel >= 0.05 else 10 if rel >= 0.01 else 5 if rel >= 0.0025 else 0
        s = sig.get(t.id, {})
        cluster = min(20, 4 * int((s.get("cluster_buy") or {}).get("members", 0) or 0))
        opt = 10 if "options" in s else 0
        anom = 5 if "anomaly" in s else 0
        conf = 5 if "conflict" in s else 0
        exc = track.get(t.member_id)
        trec = 15 if (exc is not None and exc >= 0.10) else 10 if (exc is not None and exc > 0) else 0
        score = min(100, size + size_nw + cluster + opt + anom + conf + trec)
        upsert_signal(
            db, t.id, "conviction", score,
            {"size": size, "size_vs_networth": size_nw, "cluster": cluster, "options": opt,
             "anomaly": anom, "conflict": conf, "track_record": trec},
        )
    db.commit()


def notify(db, cfg):
    ac = cfg.get("alerts", {})
    if not ac.get("enabled"):
        return 0
    min_score = int(ac.get("min_score", 3))
    cap = int(ac.get("max_per_run", 25))
    url = ac["ntfy_url"].rstrip("/") + "/" + ac.get("ntfy_topic", "congress-trades")

    watch_members = set()
    watch_tickers = set()
    for w in db.scalars(select(Watchlist)).all():
        (watch_members if w.kind == "member" else watch_tickers).add(w.value.upper() if w.kind == "ticker" else w.value)

    # candidate trades: unalerted ALERT-type signals, summed score >= min_score OR on a watchlist
    # (conviction is excluded — it's a 0-100 display score, not an alert trigger)
    rows = db.execute(
        select(Trade, Member, func.sum(TradeSignal.score), func.array_agg(TradeSignal.signal_type))
        .join(TradeSignal, TradeSignal.trade_id == Trade.id)
        .join(Member, Member.id == Trade.member_id, isouter=True)
        .where(and_(TradeSignal.alerted_at.is_(None), TradeSignal.signal_type.in_(ALERT_TYPES)))
        .group_by(Trade.id, Member.id)
        .order_by(func.sum(TradeSignal.score).desc())
        .limit(cap * 3)
    ).all()

    sent = 0
    for t, m, score, types in rows:
        on_watch = (str(t.member_id) in watch_members) or ((t.ticker or "") in watch_tickers)
        if not (score >= min_score or on_watch):
            continue
        if sent >= cap:
            break
        who = m.full_name if m else "Unknown"
        party = f" ({m.party[0]}-{m.state})" if (m and m.party and m.state) else ""
        direction = (t.transaction_type or "").upper()
        msg = f"{who}{party} {direction} {t.ticker or t.asset_name or '?'} {t.amount_range_raw or ''}".strip()
        tags = ",".join(sorted(set(types)))[:120]
        try:
            requests.post(
                url,
                data=msg.encode("utf-8"),
                headers={
                    "Title": f"Congress trade: {t.ticker or 'filing'}",
                    "Tags": "chart_with_upwards_trend",
                    "Priority": "high" if (score or 0) >= 4 else "default",
                    "X-Signals": tags,
                },
                timeout=15,
            )
            db.execute(
                text("UPDATE trade_signals SET alerted_at = now() WHERE trade_id = :tid AND alerted_at IS NULL"),
                {"tid": t.id},
            )
            sent += 1
        except Exception as e:  # noqa: BLE001
            print(f"ntfy push failed for trade {t.id}: {e}")
            break
    db.commit()
    return sent


def run():
    cfg = load_config()
    init_db()
    db = SessionLocal()
    try:
        scored = compute(db, cfg)
        sent = notify(db, cfg)
        common.record_run(db, "signals", rows_upserted=scored, success=True)
        print(f"signals: scored {scored}, alerted {sent}")
    except Exception as e:  # noqa: BLE001
        common.record_run(db, "signals", success=False, note=str(e))
        print(f"signals: FAILED {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    run()
