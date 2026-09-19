from scripts.arb_scan import summarize as arb_summarize
from scripts.mm_replay import Ticks, queue_ahead_at, simulate, summarize
from src.mm.quoter import QuoterParams

W = 300
PARAMS = QuoterParams(half_spread=0.02, size=5.0)


def _ticks(spot=100.0, strike=100.0):
    return Ticks([{"ts": float(t), "spot": spot, "strike": strike} for t in range(W, W + 300)])


def _snap(ts, k, bid, ask, bsz=10.0):
    return {"t": "s", "ts": float(ts), "w": W, "k": k, "b": [[bid, bsz]], "a": [[ask, 5.0]]}


def _trade(ts, k, price, size, d):
    return {"t": "t", "ts": float(ts), "w": W, "k": k, "p": price, "s": size, "d": d}


def test_queue_ahead_rules():
    assert queue_ahead_at([[0.40, 10.0], [0.39, 5.0]], 0.40) == 10.0    # joins at best: full queue
    assert queue_ahead_at([[0.40, 10.0], [0.39, 5.0]], 0.41) == 0.0     # improves the bid: front
    assert queue_ahead_at([[0.40, 10.0], [0.39, 5.0]], 0.30) == 15.0    # deeper than shown: all visible
    assert queue_ahead_at([], 0.40) == 0.0


def test_sell_through_our_bid_fills_and_higher_prints_do_not():
    # spot == strike, tau 200s => fair 0.5 => bid 0.48 on both tokens
    ev = [_snap(400, "Y", 0.47, 0.50), _snap(400, "N", 0.47, 0.50),
          _trade(401, "Y", 0.47, 50, "S"),      # below our 0.48: fills us (5 shares)
          _trade(403, "Y", 0.49, 50, "S")]      # above our bid: no fill
    fills, _ = simulate(ev, _ticks(), PARAMS)
    assert len(fills) == 1 and fills[0]["k"] == "Y"
    assert fills[0]["price"] == 0.48
    assert fills[0]["shares"] == 5.0


def test_no_quote_when_spot_jumps_and_no_fill_after_pull():
    ticks = Ticks([{"ts": 398.0, "spot": 100.0, "strike": 100.0}, {"ts": 400.0, "spot": 100.5, "strike": 100.0}])
    ev = [_snap(400, "Y", 0.47, 0.50), _trade(401, "Y", 0.40, 50, "S")]
    fills, _ = simulate(ev, ticks, PARAMS)
    assert fills == []                            # 50 bps move in 2s => quotes pulled


def test_summarize_markout_and_settlement():
    ev = [_snap(400, "Y", 0.47, 0.50), _trade(401, "Y", 0.47, 50, "S"),
          _snap(440, "Y", 0.60, 0.62), _snap(590, "Y", 0.97, 0.99)]
    fills, mids = simulate(ev, _ticks(), PARAMS)
    rows = summarize(fills, mids)
    assert len(rows) == 1
    price = fills[0]["price"]
    assert abs(rows[0]["m30"] - (0.61 - price)) < 1e-9       # mid at +30s (440s snap) minus fill price
    assert abs(rows[0]["settle"] - (1 - price)) < 1e-9       # last YES mid > 0.9 => resolved up
    assert rows[0]["rebate"] > 0


def test_arb_summary_and_kill_rule():
    eps = [{"start": 0.0, "end": 0.4, "dur": 0.4, "gross": 0.03, "net": 0.01, "shares": 10}]
    s = arb_summarize(eps)
    assert s["episodes"] == 1 and s["kill"] is True and s["hist"]["0-0.5s"] == 1
    long_eps = [{"start": i * 3600.0, "end": i * 3600.0 + 3.0, "dur": 3.0, "gross": 0.05, "net": 0.02, "shares": 20}
                for i in range(50)]
    assert arb_summarize(long_eps)["kill"] is False
    assert arb_summarize([]) is None
