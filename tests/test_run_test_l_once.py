import os
import time

import scripts.run_test_l_once as h


def _setup(tmp_path, monkeypatch, n_chunks, age_sec=600, lock=False):
    fresh = tmp_path / "data" / "trades2"
    fresh.mkdir(parents=True)
    for i in range(n_chunks):
        p = fresh / f"chunk_{i}.csv.gz"
        p.write_bytes(b"x")
        os.utime(p, (time.time() - age_sec, time.time() - age_sec))
    lock_path = tmp_path / "data" / "L_RAN.lock"
    if lock:
        lock_path.write_text("started")
    monkeypatch.setattr(h, "ROOT", str(tmp_path))
    monkeypatch.setattr(h, "LOCK", str(lock_path))


def test_refuses_when_chunks_missing(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, 3)
    assert "3/5 chunks" in h.preflight(5)


def test_refuses_when_lock_exists(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, 5, lock=True)
    assert "already evaluated" in h.preflight(5)


def test_refuses_while_download_still_writing(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, 5, age_sec=5)
    assert "may still be running" in h.preflight(5)


def test_allows_complete_quiet_sample(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, 5)
    assert h.preflight(5) is None


def test_lock_written_and_second_run_refused(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, 5)
    (tmp_path / "FREEZE.md").write_text("f")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "late_lock_test.py").write_text("print('VERDICT L: KILL')\n")
    monkeypatch.setattr("sys.argv", ["run_test_l_once.py", "--expected", "5"])
    assert h.main() == 0
    assert os.path.exists(h.LOCK)
    results = [f for f in os.listdir(tmp_path / "data") if f.startswith("L_result_")]
    assert len(results) == 1
    assert "VERDICT L: KILL" in (tmp_path / "data" / results[0]).read_text()
    assert h.main() == 2
    assert len([f for f in os.listdir(tmp_path / "data") if f.startswith("L_result_")]) == 1
