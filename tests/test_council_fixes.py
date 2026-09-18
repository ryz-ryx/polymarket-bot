import csv
from types import SimpleNamespace

from config import config
from scripts.forward_test_report import timeboxed_verdict
from src.bot import AssetTradingEngine


def test_timebox_stops_inconclusive_test_after_deadline():
    assert timeboxed_verdict("KEEP-GOING", 61, 60) == "STOP-TIMEBOX"
    assert timeboxed_verdict("KEEP-GOING", 10, 60) == "KEEP-GOING"


def test_timebox_never_overrides_a_real_verdict():
    assert timeboxed_verdict("PASS", 90, 60) == "PASS"
    assert timeboxed_verdict("FAIL", 90, 60) == "FAIL"


def test_paper_min_entry_tau_default_blocks_last_seconds():
    assert config.paper_min_entry_tau_sec >= 10.0


def test_oracle_divergence_log_writes_header_and_rows(tmp_path):
    fake = SimpleNamespace(oracle_log_path=str(tmp_path / "oracle_divergence.csv"))
    item = {"window_id": 1, "slug": "btc-updown-5m-300", "strike_k": 100.0,
            "snapshot_price": 101.0, "twap_30": 100.5, "twap_60": 100.2, "binance_estimate": 1}
    AssetTradingEngine._log_oracle_divergence(fake, item, 1)
    AssetTradingEngine._log_oracle_divergence(fake, {**item, "window_id": 2}, 0)
    rows = list(csv.DictReader(open(fake.oracle_log_path, encoding="utf-8")))
    assert [r["diverged"] for r in rows] == ["0", "1"]
    assert rows[0]["slug"] == "btc-updown-5m-300"
