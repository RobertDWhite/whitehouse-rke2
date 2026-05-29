def trade_dict(t, m=None):
    return {
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
        "owner": t.owner,
        "ticker": t.ticker,
        "asset_name": t.asset_name,
        "asset_type": t.asset_type,
        "transaction_type": t.transaction_type,
        "amount_min": float(t.amount_min) if t.amount_min is not None else None,
        "amount_max": float(t.amount_max) if t.amount_max is not None else None,
        "amount_range": t.amount_range_raw,
        "cap_gains_over_200": t.cap_gains_over_200,
        "comment": t.comment,
    }


def member_dict(m, trade_count=None):
    d = {
        "id": m.id,
        "full_name": m.full_name,
        "chamber": m.chamber,
        "party": m.party,
        "state": m.state,
        "district": m.district,
    }
    if trade_count is not None:
        d["trade_count"] = trade_count
    return d
