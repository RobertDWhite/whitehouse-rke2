from ingest.ticker_aliases import _match, _norm


def test_norm_strips_corporate_words():
    assert _norm("NVIDIA Corporation Common Stock") == "nvidia"


def test_match_prefers_high_confidence_alias():
    aliases = [("NVIDIA CORPORATION", "NVDA", 0.95)]
    assert _match("NVIDIA Corp. Common Stock", aliases) == ("NVDA", 0.95, "NVIDIA CORPORATION")


def test_match_rejects_tiny_asset_name():
    assert _match("A", [("APPLE INC", "AAPL", 0.95)]) is None
