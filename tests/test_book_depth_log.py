import json

from src.bot import AssetTradingEngine


def _engine(tmp_path):
    e = AssetTradingEngine.__new__(AssetTradingEngine)
    e.book_depth_log_path = str(tmp_path / "book_depth_log.jsonl")
    e.asset = "BTC"
    e.current_window_id = 42
    return e


def test_logs_sorted_top5_pre_and_post_latency(tmp_path):
    e = _engine(tmp_path)
    pre = [{"price": p, "size": 10.0} for p in (0.60, 0.51, 0.55, 0.52, 0.53, 0.54, 0.56)]
    post = [{"price": 0.52, "size": 3.0}]
    e._log_book_depth("SIGNAL", "YES", 1.5, 0.52, pre, post)
    row = json.loads((tmp_path / "book_depth_log.jsonl").read_text().splitlines()[0])
    assert row["status"] == "SIGNAL" and row["window_id"] == 42 and row["vwap_price"] == 0.52
    assert [p for p, _ in row["asks_pre_latency"]] == [0.51, 0.52, 0.53, 0.54, 0.55]
    assert row["asks_post_latency"] == [[0.52, 3.0]]


def test_never_raises_on_bad_input(tmp_path):
    e = _engine(tmp_path)
    e._log_book_depth("PHANTOM", "NO", 1.0, None, None, [{"price": "x"}, "junk"])
    row = json.loads((tmp_path / "book_depth_log.jsonl").read_text().splitlines()[0])
    assert row["vwap_price"] is None and row["asks_pre_latency"] == [] and row["asks_post_latency"] == []
