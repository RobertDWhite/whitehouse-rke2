def trade_dict(t, m=None, filing=None, signals=None, price=None):
    lag = None
    if t.transaction_date and t.disclosure_date:
        lag = (t.disclosure_date - t.transaction_date).days

    amt_min = float(t.amount_min) if t.amount_min is not None else None
    amt_max = float(t.amount_max) if t.amount_max is not None else None
    # implied share count from the disclosed lower bound when we have a price
    est_shares = round(amt_min / price) if (price and price > 0 and amt_min) else None

    d = {
        "id": t.id,
        "source": t.source,
        "chamber": t.chamber,
        "member_id": t.member_id,
        "member": m.full_name if m else None,
        "party": m.party if m else None,
        "state": m.state if m else None,
        "district": m.district if m else None,
        "transaction_date": t.transaction_date.isoformat() if t.transaction_date else None,
        "disclosure_date": t.disclosure_date.isoformat() if t.disclosure_date else None,
        "disclosure_lag_days": lag,
        "owner": t.owner,
        "ticker": t.ticker,
        "asset_name": t.asset_name,
        "asset_type": t.asset_type,
        "transaction_type": t.transaction_type,
        "amount_min": amt_min,
        "amount_max": amt_max,
        "amount_range": t.amount_range_raw,
        "cap_gains_over_200": t.cap_gains_over_200,
        "comment": t.comment,
        "source_url": filing.source_url if filing else None,
        "signals": signals if signals is not None else None,
        "price": float(price) if price is not None else None,
        "est_shares": est_shares,
    }
    return d


def member_dict(m, trade_count=None):
    d = {
        "id": m.id,
        "full_name": m.full_name,
        "chamber": m.chamber,
        "party": m.party,
        "state": m.state,
        "district": m.district,
        "net_worth_min": float(m.net_worth_min) if m.net_worth_min is not None else None,
        "net_worth_max": float(m.net_worth_max) if m.net_worth_max is not None else None,
        "net_worth_year": m.net_worth_year,
    }
    if trade_count is not None:
        d["trade_count"] = trade_count
    return d
