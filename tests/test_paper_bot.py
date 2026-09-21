import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import paper_bot as pb  # noqa: E402


def _closes(n=1800, spike=True, seed=1):
    rng = np.random.default_rng(seed)
    px = 100 * np.exp(np.cumsum(rng.normal(0, 0.0003, n)))
    if spike:
        px[-1] *= 1.02  # last closed bar: large 15-minute jump -> momentum signal
    return list(px)


def test_no_signal_no_trade():
    st = pb.new_state()
    ev = pb.on_bar_close(st, "BTCUSDT", _closes(spike=False), 100.0, 1_000)
    assert ev == [] and st["cash"] == 50.0 and st["trades"] == 0


def test_enter_at_ask_exit_at_bid_charges_fees_and_spread():
    st = pb.new_state()
    ev = pb.on_bar_close(st, "BTCUSDT", _closes(), 100.02, 1_000)  # ask above bid: spread is a real cost
    assert ev and ev[0]["action"] == "enter" and ev[0]["px"] == 100.02 and st["fees"] > 0
    assert pb.on_tick(st, "BTCUSDT", 99.99, 100.01, 2_000) == []  # hold not over yet
    hold = st["pos"]["BTCUSDT"]["exit_ts"]
    out = pb.on_tick(st, "BTCUSDT", 100.00, 100.02, hold)  # bid 100.00 < entry ask 100.02
    assert out and out[0]["action"] == "exit" and "BTCUSDT" not in st["pos"]
    assert st["closed_pnl"] < 0  # flat mid price: fees and spread lost
    assert st["cash"] < 50.0


def test_position_size_respects_cap():
    st = pb.new_state()
    pb.on_bar_close(st, "BTCUSDT", _closes(), 100.0, 1_000)
    assert st["cash"] >= 50.0 * (1 - pb.SIZE_FRAC) - 0.5


def test_needs_enough_closed_bars():
    st = pb.new_state()
    assert pb.on_bar_close(st, "BTCUSDT", _closes()[-500:], 100.0, 1_000) == []  # fewer than BARS_NEEDED
    assert pb.PAGES * 1000 > pb.BARS_NEEDED  # regression: 1000 bars once made the bot unable to ever trade


def test_equity_values_each_position_at_its_own_price():
    st = pb.new_state()
    st["cash"] = 10.0
    st["pos"]["BTCUSDT"] = {"rule": "R3_breakout", "qty": 0.5, "cost_basis": 50.0, "exit_ts": 0}
    st["mark"] = {"BTCUSDT": 100.0, "ETHUSDT": 2.0}
    assert abs(pb.equity(st) - 60.0) < 1e-9  # 10 cash + 0.5 * BTC price, not the ETH price


def test_stop_rule_halts_at_stop_equity():
    st = pb.new_state()
    prereg = {"stop_equity": 40.0}
    assert pb.stop_reason(st, prereg) is None
    st["cash"] = 39.0
    assert "stop" in pb.stop_reason(st, prereg)


def test_cost_split_adds_up_to_net_pnl():
    import paper_report as pr
    st = pb.new_state()
    pb.on_bar_close(st, "BTCUSDT", _closes(), 100.02, 1_000, bid=100.00)
    hold = st["pos"]["BTCUSDT"]["exit_ts"]
    ev = pb.on_tick(st, "BTCUSDT", 100.20, 100.22, hold)[0]
    n, gross, spread, fees, net = next(iter(pr.cost_split([ev]).values()))
    assert abs(gross - spread - fees - net) < 1e-9 and spread > 0 and fees > 0
    assert abs(net - st["closed_pnl"]) < 1e-3


def test_second_coin_signal_uses_own_position_only():
    st = pb.new_state()
    pb.on_bar_close(st, "BTCUSDT", _closes(), 100.0, 1_000)
    pb.on_bar_close(st, "ETHUSDT", _closes(seed=2), 50.0, 1_000)
    assert set(st["pos"]) == {"BTCUSDT", "ETHUSDT"}
    assert st["cash"] >= 0
