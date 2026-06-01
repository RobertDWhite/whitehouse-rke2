import datetime as dt

from ingest import normalize as nz


def test_norm_name_order_insensitive():
    assert nz.norm_name("Tuberville, Tommy") == nz.norm_name("Tommy Tuberville")


def test_parse_open_ended_amount():
    assert nz.parse_amount("Over $50,000,000") == (50_000_000, None, "Over $50,000,000")


def test_dedup_ignores_amount_formatting():
    a = nz.dedup_key("house", "jane doe", dt.date(2026, 1, 2), "NVDA", 1000, 15000, "purchase")
    b = nz.dedup_key("house", "jane doe", dt.date(2026, 1, 2), "NVDA", 1000, 15000, "purchase")
    assert a == b


def test_clean_ticker_rejects_junk():
    assert nz.clean_ticker("NVDA") == "NVDA"
    assert nz.clean_ticker("not a ticker") is None
