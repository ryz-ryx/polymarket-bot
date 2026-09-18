from src.calibrator import EmpiricalCalibrator


def test_cold_start_weight_default(tmp_path, monkeypatch):
    monkeypatch.delenv("COLD_START_CONFIDENCE", raising=False)
    cal = EmpiricalCalibrator(log_path=str(tmp_path / "missing.csv"))
    assert cal.get_confidence_weight() == 0.5


def test_cold_start_weight_env_override_and_clamp(tmp_path, monkeypatch):
    cal = EmpiricalCalibrator(log_path=str(tmp_path / "missing.csv"))
    monkeypatch.setenv("COLD_START_CONFIDENCE", "2")
    assert cal.get_confidence_weight() == 1.0
