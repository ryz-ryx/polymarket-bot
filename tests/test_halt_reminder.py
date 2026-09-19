from src import bot as bot_mod
from src.bot import HALT_REMINDER_SEC, Polymarket5mBot


def _mgr(drawdown=False, daily=False, last=0.0):
    m = Polymarket5mBot.__new__(Polymarket5mBot)
    m.drawdown_breaker_triggered = drawdown
    m.portfolio_circuit_breaker = daily
    m._last_halt_reminder = last
    return m


def test_no_reminder_when_not_halted(monkeypatch):
    sent = []
    monkeypatch.setattr(bot_mod.notifier, "alert", sent.append)
    assert _mgr()._halt_reminder(now=10 * HALT_REMINDER_SEC) is False
    assert sent == []


def test_reminds_once_per_interval_while_halted(monkeypatch):
    sent = []
    monkeypatch.setattr(bot_mod.notifier, "alert", sent.append)
    m = _mgr(drawdown=True, last=1000.0)
    assert m._halt_reminder(now=1000.0 + HALT_REMINDER_SEC - 1) is False
    assert m._halt_reminder(now=1000.0 + HALT_REMINDER_SEC) is True
    assert m._halt_reminder(now=1000.0 + HALT_REMINDER_SEC + 5) is False
    assert len(sent) == 1 and "STILL HALTED" in sent[0] and "drawdown" in sent[0]
