import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import paper_bot as pb  # noqa: E402


def _bars(n=1800, spike=True, seed=1):
    rng = np.random.default_rng(seed)
    px = 100 * np.exp(np.cumsum(rng.normal(0, 0.0003, n)))
    if spike:
        px[-2] *= 1.02  # last closed bar: large 15-min jump -> momentum signal
    ts = 1_800_000_000_000 + np.arange(n) * 60_000
    return np.column_stack([ts, px, px])


def test_no_signal_no_trade():
    st = pb.new_state()
    ev = pb.step(st, "BTCUSDT", _bars(spike=False))
    assert ev == [] and st["cash"] == 50.0 and st["trades"] == 0


def test_enter_then_exit_charges_fees_both_sides():
    st = pb.new_state()
    bars = _bars()
    ev = pb.step(st, "BTCUSDT", bars)
    assert ev and ev[0]["action"] == "enter" and st["fees"] > 0
    equity_after_entry = st["cash"] + st["pos"]["BTCUSDT"]["qty"] * bars[-1, 1]
    assert equity_after_entry < 50.0  # entry fee already paid
    later = bars.copy()
    later[:, 0] += 61 * 60_000  # jump past the hold window at the same price
    later[-2, 1:] = later[-2, 1] / 1.02  # remove spike so no immediate re-entry signal
    ev2 = pb.step(st, "BTCUSDT", later)
    assert any(e["action"] == "exit" for e in ev2)
    assert st["closed_pnl"] < 0  # flat price: only fees lost


def test_position_size_respects_cap():
    st = pb.new_state()
    pb.step(st, "BTCUSDT", _bars())
    assert st["cash"] >= 50.0 * (1 - pb.SIZE_FRAC) - 0.5
