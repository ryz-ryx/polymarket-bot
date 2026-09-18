import json

from config import config
from scripts.forward_test_report import bootstrap_profit_rate_ci, load_resolved_trades, verdict


def test_config_only_trades_proven_assets():
    assert set(config.target_assets) <= set(config.proven_assets)
    assert "ETH" not in config.target_assets and "SOL" not in config.target_assets


def test_load_resolved_trades_filters_asset_type_and_freeze_time(tmp_path):
    rows = [
        {"ts": 50, "asset": "BTC", "type": "WIN", "cost": 2.0, "net_pnl": 1.0},   # before freeze
        {"ts": 150, "asset": "BTC", "type": "WIN", "cost": 2.0, "net_pnl": 1.5},
        {"ts": 160, "asset": "BTC", "type": "LOSS", "cost": 2.0, "net_pnl": -2.0},
        {"ts": 170, "asset": "ETH", "type": "WIN", "cost": 2.0, "net_pnl": 1.0},  # wrong asset
        {"ts": 180, "asset": "BTC", "type": "BUY", "price": 0.5},                 # not resolved
    ]
    f = tmp_path / "fills.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in rows))
    assert load_resolved_trades(str(f), since_ts=100) == [(2.0, 1.5), (2.0, -2.0)]


def test_verdict_needs_min_trades_and_ci_clear_of_zero():
    assert verdict(10, 0.5, 0.9, 0.7, 300) == "KEEP-GOING"
    assert verdict(300, 0.01, 0.2, 0.1, 300) == "PASS"
    assert verdict(300, -0.3, -0.05, -0.2, 300) == "FAIL"
    assert verdict(300, -0.1, 0.2, 0.05, 300) == "KEEP-GOING"


def test_bootstrap_ci_brackets_point_estimate():
    trades = [(1.0, 0.5)] * 60 + [(1.0, -1.0)] * 40
    lo, hi = bootstrap_profit_rate_ci(trades, n_boot=2000)
    assert lo < (60 * 0.5 - 40) / 100 < hi
