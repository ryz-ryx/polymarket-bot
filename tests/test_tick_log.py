import json

from src.bot import AssetTradingEngine


def _engine(tmp_path):
    e = AssetTradingEngine.__new__(AssetTradingEngine)
    e.tick_log_path = str(tmp_path / "tick_log.jsonl")
    e.asset = "BTC"
    e.current_window_id = 7
    e.strike_price = 100000.0
    return e


def test_logs_spot_and_top_of_book(tmp_path):
    e = _engine(tmp_path)
    e._log_tick(100010.5, {"yes_bid": 0.48, "yes_ask": 0.5, "no_bid": 0.5, "no_ask": 0.52})
    row = json.loads((tmp_path / "tick_log.jsonl").read_text().splitlines()[0])
    assert row["spot"] == 100010.5 and row["window_id"] == 7 and row["yes_ask"] == 0.5


def test_never_raises_on_bad_input(tmp_path):
    e = _engine(tmp_path)
    e._log_tick(1.0, None)
    e.tick_log_path = str(tmp_path)  # a directory: open() fails
    e._log_tick(1.0, {})
