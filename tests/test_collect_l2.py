import gzip
import json
import os

from scripts.collect_l2 import Collector, Recorder
from src.mm.arb import ArbTracker
from src.mm.book import LocalBook


def _collector(tmp_path):
    c = Collector.__new__(Collector)
    c.rec = Recorder(str(tmp_path), 100)
    c.arb = ArbTracker()
    c.windows = {300: {"Y": "yes", "N": "no"}}
    c.tokens = {"yes": (300, "Y"), "no": (300, "N")}
    c.books = {"yes": LocalBook(), "no": LocalBook()}
    c.last_snap = {}
    c.stats = {"events": {}, "trades": 0, "snaps": 0, "arb_episodes": 0, "last_event_ts": None}
    c.arb_path = str(tmp_path / "arb_log.jsonl")
    return c


def _book(tok, bids, asks):
    return {"event_type": "book", "asset_id": tok, "bids": [{"price": str(p), "size": str(s)} for p, s in bids],
            "asks": [{"price": str(p), "size": str(s)} for p, s in asks]}


def test_book_change_trade_and_snapshot_records(tmp_path):
    c = _collector(tmp_path)
    c.handle(_book("yes", [(0.40, 10)], [(0.42, 5)]), 1000.0)
    c.handle({"event_type": "price_change", "price_changes": [
        {"asset_id": "yes", "price": "0.41", "size": "3", "side": "SELL"}]}, 1000.1)
    assert c.books["yes"].best_ask() == (0.41, 3.0)
    c.handle({"event_type": "last_trade_price", "asset_id": "yes", "price": "0.41", "size": "2",
              "side": "BUY", "timestamp": "1700000000000"}, 1000.2)
    c.snapshots(1000.3)
    c.rec.flush()
    rows = [json.loads(line) for f in os.listdir(tmp_path) if f.startswith("l2_")
            for line in open(tmp_path / f, encoding="utf-8")]
    t = [r for r in rows if r["t"] == "t"][0]
    s = [r for r in rows if r["t"] == "s"][0]
    assert t["d"] == "B" and t["p"] == 0.41 and t["k"] == "Y" and t["w"] == 300
    assert s["b"] == [[0.4, 10.0]] and s["a"] == [[0.41, 3.0], [0.42, 5.0]]


def test_arb_episode_written_when_pair_sums_below_one(tmp_path):
    c = _collector(tmp_path)
    c.handle(_book("yes", [], [(0.45, 20)]), 10.0)
    c.handle(_book("no", [], [(0.50, 20)]), 10.5)   # 0.45 + 0.50 < 1 -> episode opens
    c.handle(_book("no", [], [(0.60, 20)]), 11.0)   # closes
    rows = [json.loads(line) for line in open(c.arb_path, encoding="utf-8")]
    assert len(rows) == 1 and rows[0]["dur"] == 0.5 and rows[0]["shares"] == 20


def test_unknown_tokens_and_bad_trades_are_ignored(tmp_path):
    c = _collector(tmp_path)
    c.handle(_book("other", [(0.4, 1)], []), 1.0)
    c.handle({"event_type": "last_trade_price", "asset_id": "yes", "price": "x", "size": "1"}, 1.0)
    assert c.stats["trades"] == 0


def test_recorder_rotates_compresses_and_caps(tmp_path):
    rec = Recorder(str(tmp_path), 0.0001)   # ~100 bytes cap: smaller than any two gz files
    for h in range(4):
        for i in range(20):
            rec.write({"t": "s", "ts": 3600.0 * h + i, "w": 300, "k": "Y", "b": [], "a": []})
    rec.flush()
    files = sorted(f for f in os.listdir(tmp_path) if f.startswith("l2_"))
    assert files[-1].endswith(".jsonl")               # newest stays uncompressed
    assert all(f.endswith(".gz") for f in files[:-1])
    assert len(files) < 4                              # oldest were deleted by the cap
    gz = [f for f in files if f.endswith(".gz")]
    if gz:
        assert gzip.open(tmp_path / gz[0], "rt").readline().startswith("{")
