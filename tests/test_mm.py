from src.mm.arb import ArbTracker, pair_edge
from src.mm.book import LocalBook
from src.mm.fills import RestingBid
from src.mm.quoter import QuoterParams, fair_prob_up, quote_bid, should_pull


def test_book_snapshot_then_deltas():
    b = LocalBook()
    b.apply_snapshot([{"price": "0.40", "size": "10"}, {"price": "0.39", "size": "5"}],
                     [{"price": "0.42", "size": "7"}, {"price": "0.45", "size": "3"}])
    assert b.best_bid() == (0.40, 10.0) and b.best_ask() == (0.42, 7.0)
    b.apply_change("SELL", "0.41", "4")
    assert b.best_ask() == (0.41, 4.0)
    b.apply_change("BUY", "0.40", "0")
    assert b.best_bid() == (0.39, 5.0)
    bids, asks = b.top(2)
    assert bids == [[0.39, 5.0]] and asks == [[0.41, 4.0], [0.42, 7.0]]


def test_book_ignores_malformed():
    b = LocalBook()
    b.apply_snapshot([{"price": "x", "size": "1"}, {"nope": 1}], None)
    b.apply_change("BUY", "abc", "1")
    assert b.best_bid() is None and b.best_ask() is None


def test_pair_edge_fees():
    gross, net = pair_edge(0.48, 0.48)
    assert abs(gross - 0.04) < 1e-9 and net < gross


def test_arb_episode_open_and_close():
    t = ArbTracker()
    assert t.update(1, 10.0, (0.48, 20), (0.48, 50)) is None
    assert t.update(1, 10.5, (0.47, 30), (0.48, 50)) is None
    ep = t.update(1, 11.0, (0.52, 20), (0.50, 50))
    assert ep and ep["dur"] == 1.0 and abs(ep["gross"] - 0.05) < 1e-9 and ep["shares"] == 30
    assert t.update(1, 12.0, (0.52, 20), (0.50, 50)) is None


def test_arb_flush_closes_open_episodes():
    t = ArbTracker()
    t.update(7, 1.0, (0.4, 5), (0.4, 5))
    eps = t.flush(2.0)
    assert len(eps) == 1 and eps[0]["dur"] == 1.0


def test_fair_prob_and_quote():
    assert abs(fair_prob_up(100.0, 100.0, 120, 0.5) - 0.5) < 1e-9
    assert fair_prob_up(101.0, 100.0, 120, 0.5) > 0.9
    p = QuoterParams()
    q = quote_bid(0.50, 0.0, 120, p)
    assert q["price"] == 0.48 and q["size"] == 5.0
    assert quote_bid(0.50, 0.0, 10, p) is None       # too close to expiry
    assert quote_bid(0.50, 20.0, 120, p) is None      # inventory cap
    assert quote_bid(0.50, 10.0, 120, p)["price"] < q["price"]  # inventory skews the bid down
    assert quote_bid(0.02, 0.0, 120, p) is None       # outside price band


def test_pull_on_spot_move():
    p = QuoterParams()
    assert should_pull(100.05, 100.0, p) is (5.0 > p.pull_move_bps)
    assert should_pull(100.01, 100.0, p) is False


def test_fill_model_is_conservative():
    bid = RestingBid(price=0.40, size=5.0, queue_ahead=10.0)
    assert bid.on_trade(0.41, 100, "S") == 0.0          # above our price: no fill
    assert bid.on_trade(0.40, 100, "B") == 0.0          # taker buy never hits a bid
    assert bid.on_trade(0.40, 8, "S") == 0.0            # eaten by queue ahead
    assert bid.queue_ahead == 2.0
    assert bid.on_trade(0.40, 5, "S") == 3.0            # 2 clears queue, 3 fills us
    assert bid.on_trade(0.39, 100, "S") == 2.0          # through our price fills the rest
    assert bid.remaining == 0.0 and bid.on_trade(0.30, 5, "S") == 0.0
