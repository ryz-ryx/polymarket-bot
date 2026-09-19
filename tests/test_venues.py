from src.mm.venues import Throttle, parse_binance, parse_coinbase, parse_kraken


def test_parse_binance():
    assert parse_binance({"u": 1, "s": "BTCUSDT", "b": "81436.00", "B": "3", "a": "81436.01", "A": "1"}) == (81436.0, 81436.01, None)
    assert parse_binance({"b": "x", "a": "1"}) is None
    assert parse_binance({"b": "100", "a": "99"}) is None          # crossed quote rejected
    assert parse_binance({}) is None


def test_parse_coinbase():
    m = {"type": "ticker", "product_id": "BTC-USD", "best_bid": "81425.87", "best_ask": "81425.88",
         "time": "2026-09-19T18:52:56.910535Z"}
    bid, ask, st = parse_coinbase(m)
    assert (bid, ask) == (81425.87, 81425.88) and st is not None and st > 1.7e9
    assert parse_coinbase({"type": "subscriptions"}) is None
    assert parse_coinbase({"type": "ticker", "best_bid": "a", "best_ask": "b"}) is None


def test_parse_kraken():
    m = {"channel": "ticker", "type": "update",
         "data": [{"symbol": "BTC/USD", "bid": 81421.8, "ask": 81421.9, "timestamp": "2026-09-19T18:52:51.459794Z"}]}
    bid, ask, st = parse_kraken(m)
    assert (bid, ask) == (81421.8, 81421.9) and st is not None
    assert parse_kraken({"channel": "status", "type": "update", "data": []}) is None
    assert parse_kraken({"channel": "ticker", "type": "update", "data": []}) is None


def test_throttle_drops_unchanged_and_too_fast():
    t = Throttle(0.25)
    assert t.allow(0.0, 100.0, 100.1) is True
    assert t.allow(1.0, 100.0, 100.1) is False        # unchanged
    assert t.allow(1.1, 100.5, 100.6) is True
    assert t.allow(1.2, 100.7, 100.8) is False        # changed but under 250ms
    assert t.allow(1.4, 100.7, 100.8) is True
