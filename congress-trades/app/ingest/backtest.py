"""Backtest 'follow-strategy' portfolios + a live smart-money basket, cached in strategy_runs.

Honest by construction: every position enters at the first close ON/AFTER the public disclosure
date (bakes in the up-to-45-day STOCK Act lag), price-return only, equal/notional/conviction
weighted, benchmarked dollar-for-dollar against SPY (and NANC, the Democratic-Congress ETF).
It models a $1-per-disclosed-buy, hold-to-now book — a follower's reality, not the member's."""
import bisect
import datetime as dt

from sqlalchemy import and_, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import load_config
from app.db import SessionLocal, init_db
from app.models import Member, StrategyRun, TickerBar, Trade, TradeSignal

from . import common

START = dt.date(2023, 1, 1)


def _load_bars(db):
    """ticker -> (sorted dates, closes) for forward-fill lookups."""
    bars = {}
    for tk, d, c in db.execute(select(TickerBar.ticker, TickerBar.bar_date, TickerBar.close).order_by(TickerBar.ticker, TickerBar.bar_date)):
        b = bars.setdefault(tk, ([], []))
        b[0].append(d)
        b[1].append(float(c))
    return bars


def _close_on(bars, ticker, d):
    """Last close on/before d (forward-fill); None if no prior bar."""
    rec = bars.get(ticker)
    if not rec:
        return None
    dates, closes = rec
    i = bisect.bisect_right(dates, d) - 1
    return closes[i] if i >= 0 else None


def _positions(db, bars, where):
    """[(ticker, entry_date, entry_price, weight)] for purchases matching `where`."""
    rows = db.execute(
        select(Trade.ticker, Trade.disclosure_date, Trade.entry_price,
               (func.coalesce(Trade.amount_min, 0) + func.coalesce(Trade.amount_max, Trade.amount_min, 0)) / 2.0)
        .where(and_(Trade.transaction_type == "purchase", Trade.ticker.isnot(None), Trade.disclosure_date.isnot(None), where))
    ).all()
    out = []
    for tk, dd, ep, mid in rows:
        ep = float(ep) if ep is not None else _close_on(bars, tk, dd)
        if ep and ep > 0 and tk in bars:
            out.append((tk, dd, ep, float(mid or 1)))
    return out


def _curve(bars, positions, weighting):
    """Weekly normalized portfolio index vs a dollar-matched SPY index."""
    if not positions:
        return [], {}
    spy = bars.get("SPY")
    today = dt.date.today()
    start = max(min(p[1] for p in positions), START)
    # weekly grid
    days = []
    d = start
    while d <= today:
        days.append(d)
        d += dt.timedelta(days=7)
    curve = []
    for d in days:
        num = wsum = snum = 0.0
        for tk, entry, ep, mid in positions:
            if entry > d:
                continue
            w = 1.0 if weighting == "equal" else mid
            px = _close_on(bars, tk, d)
            spx = _close_on(bars, "SPY", d)
            spe = _close_on(bars, "SPY", entry)
            if not px:
                continue
            num += w * (px / ep)
            wsum += w
            if spx and spe:
                snum += w * (spx / spe)
        if wsum:
            curve.append([d.isoformat(), round(num / wsum, 4), round(snum / wsum, 4)])
    metrics = {}
    if curve:
        vals = [c[1] for c in curve]
        peak = vals[0]
        mdd = 0.0
        for v in vals:
            peak = max(peak, v)
            mdd = min(mdd, v / peak - 1)
        last = curve[-1]
        yrs = max((dt.date.fromisoformat(last[0]) - dt.date.fromisoformat(curve[0][0])).days / 365.25, 0.1)
        metrics = {
            "total_return": last[1] - 1,
            "cagr": last[1] ** (1 / yrs) - 1 if last[1] > 0 else None,
            "max_drawdown": mdd,
            "excess_vs_spy": last[1] - last[2],
            "n_positions": len(positions),
        }
    return curve, metrics


def _smart_money(db, bars, where, limit=20):
    """Current net-accumulated basket (last 90d) for the cohort — the mirror-able watchlist."""
    since = dt.date.today() - dt.timedelta(days=90)
    mid = (func.coalesce(Trade.amount_min, 0) + func.coalesce(Trade.amount_max, Trade.amount_min, 0)) / 2.0
    from sqlalchemy import case
    net = func.sum(case((Trade.transaction_type == "purchase", mid), (Trade.transaction_type == "sale", -mid), else_=0))
    rows = db.execute(
        select(Trade.ticker, net.label("net"), func.count(func.distinct(Trade.member_id)))
        .where(and_(where, Trade.ticker.isnot(None), Trade.disclosure_date >= since))
        .group_by(Trade.ticker).having(net > 0).order_by(net.desc()).limit(limit)
    ).all()
    conv = dict(db.execute(
        select(Trade.ticker, func.max(TradeSignal.score))
        .join(TradeSignal, and_(TradeSignal.trade_id == Trade.id, TradeSignal.signal_type == "conviction"))
        .where(and_(where, Trade.disclosure_date >= since, Trade.ticker.isnot(None))).group_by(Trade.ticker)
    ).all())
    return [{"ticker": tk, "net_notional": float(nv or 0), "buyers": int(bc or 0), "conviction": conv.get(tk)} for tk, nv, bc in rows]


def run():
    cfg = load_config()
    init_db()
    db = SessionLocal()
    try:
        bars = _load_bars(db)
        # top performers (by precomputed excess) for a "follow the winners" strategy
        top_ids = [r[0] for r in db.execute(
            select(Trade.member_id)
            .where(and_(Trade.transaction_type == "purchase", Trade.return_pct.isnot(None), Trade.bench_return_pct.isnot(None)))
            .group_by(Trade.member_id)
            .having(func.count() >= 10)
            .order_by(func.avg(Trade.return_pct - Trade.bench_return_pct).desc())
            .limit(10)
        ).all()]

        conv_ids = select(TradeSignal.trade_id).where(and_(TradeSignal.signal_type == "conviction", TradeSignal.score >= 30))
        presets = [
            ("all", "All Congress", "equal", Trade.id.isnot(None)),
            ("democrat", "Democrats", "equal", Member.party == "Democrat"),
            ("republican", "Republicans", "equal", Member.party == "Republican"),
            ("top_performers", "Top-10 performers", "equal", Trade.member_id.in_(top_ids) if top_ids else Trade.id.is_(None)),
            ("high_conviction", "High-conviction basket", "conviction", Trade.id.in_(conv_ids)),
        ]
        made = 0
        for key, label, weighting, where in presets:
            base = where
            # party presets need the Member join condition applied via member_id lookup
            if key in ("democrat", "republican"):
                ids = select(Member.id).where(Member.party == ("Democrat" if key == "democrat" else "Republican"))
                base = Trade.member_id.in_(ids)
            positions = _positions(db, bars, base)
            curve, metrics = _curve(bars, positions, weighting)
            holdings = _smart_money(db, bars, base)
            db.execute(
                pg_insert(StrategyRun)
                .values(strategy_key=key, label=label, params={"weighting": weighting}, equity_curve=curve,
                        holdings=holdings, generated_at=dt.datetime.now(dt.timezone.utc), **{k: metrics.get(k) for k in
                        ("total_return", "cagr", "max_drawdown", "excess_vs_spy", "n_positions")})
                .on_conflict_do_update(
                    index_elements=["strategy_key"],
                    set_={"label": label, "equity_curve": curve, "holdings": holdings,
                          "generated_at": dt.datetime.now(dt.timezone.utc),
                          **{k: metrics.get(k) for k in ("total_return", "cagr", "max_drawdown", "excess_vs_spy", "n_positions")}},
                )
            )
            made += 1
            print(f"backtest: {key} positions={len(positions)} total_return={metrics.get('total_return')}")
        db.commit()
        common.record_run(db, "backtest", rows_upserted=made, success=True)
    except Exception as e:  # noqa: BLE001
        common.record_run(db, "backtest", success=False, note=str(e))
        raise
    finally:
        db.close()


if __name__ == "__main__":
    run()
