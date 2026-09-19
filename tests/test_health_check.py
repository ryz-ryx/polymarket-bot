import gzip
import json

from scripts.health_check import find_gaps, summarize


def _write(path, rows, gz=False):
    opener = gzip.open if gz else open
    with opener(path, "wt", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def test_find_gaps():
    assert find_gaps([0, 10, 20, 200, 210], 60) == [(20, 200, 180)]
    assert find_gaps([0, 10], 60) == []


def test_summarize_flags_short_data_and_gaps_and_stale(tmp_path):
    rows = [{"t": "s", "ts": t, "w": 300} for t in (1000.0, 1010.0, 1500.0)]
    _write(tmp_path / "l2_2026091900.jsonl.gz", rows[:2], gz=True)
    _write(tmp_path / "l2_2026091901.jsonl", rows[2:])
    s = summarize(str(tmp_path), max_gap=60, min_hours=12, now=1600.0)
    assert s["rows"] == 3 and s["windows"] == 1
    assert any("gap" in p for p in s["problems"])
    assert any("only" in p for p in s["problems"])
    assert not any("old" in p for p in s["problems"])
    stale = summarize(str(tmp_path), now=1500.0 + 1000)
    assert any("old" in p for p in stale["problems"])


def test_summarize_healthy(tmp_path):
    rows = [{"t": "s", "ts": float(t), "w": 300} for t in range(0, 13 * 3600, 30)]
    _write(tmp_path / "l2_a.jsonl", rows)
    (tmp_path / "collector_stats.json").write_text(json.dumps({"connected": True, "trades": 1}))
    s = summarize(str(tmp_path), now=rows[-1]["ts"] + 10)
    assert s["problems"] == [] and s["stats"]["connected"] is True


def test_empty_directory(tmp_path):
    assert "no L2 rows found" in summarize(str(tmp_path))["problems"]
