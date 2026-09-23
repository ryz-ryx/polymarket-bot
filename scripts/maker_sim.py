"""Test M: maker-quote simulation on the paper bot's own logged bid/ask ticks. See docs/PREREG_maker_sim.md.
Exploratory only (no PASS/KILL gate) - the fill model is a proxy, not real queue/order-book data; see the doc.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paper_report import read_ticks  # noqa: E402

HOLD_MS = 60_000
CONFIRM_MS = 2_000  # pessimistic variant: price must stay through our level this long before counting a fill


def simulate(rows, pessimistic):
    """A resting buy quote is kept at the CURRENT best bid every tick (a real maker cancel/replaces to stay at
    top of book, unlike a static order left at its first price). A fill is counted when the ask crosses to/below
    the quote we were showing just before that tick (queue proxy, see the doc); once filled, hold HOLD_MS then
    exit at the then-current best bid. rows must be time-sorted ticks for one symbol: (ts_ms, bid, ask)."""
    quote_px, cross_since, held_since = rows[0][1] if rows else None, None, None
    attempts = fills = 0
    pnl_per_fill = []
    for ts, bid, ask in rows:
        if held_since is not None:  # in a position, waiting for the hold to end
            if ts - held_since >= HOLD_MS:
                pnl_per_fill.append(bid / quote_px - 1)  # % return, so BTC and ETH are comparable
                held_since, cross_since = None, None
                quote_px = bid  # re-quote fresh after exiting
                attempts += 1
            continue
        crossed = ask <= quote_px
        if crossed:
            cross_since = ts if cross_since is None else cross_since
            if not pessimistic or ts - cross_since >= CONFIRM_MS:
                held_since = ts
                fills += 1
                continue
        else:
            cross_since = None
        quote_px = bid  # not filled yet this tick: cancel/replace to track the current best bid
    if attempts == 0:
        attempts = 1  # the whole window counts as one long-running quote if it never cycled to a fill+exit
    return attempts, fills, pnl_per_fill


def main():
    ticks = read_ticks()
    by_sym = {}
    for ts, sym, bid, ask, _ in ticks:
        by_sym.setdefault(sym, []).append((ts, bid, ask))
    for sym, rows in by_sym.items():
        rows.sort()
        for label, pessimistic in [("OPTIMISTIC", False), ("PESSIMISTIC", True)]:
            _, fills, per_fill = simulate(rows, pessimistic)
            mean_fill = sum(per_fill) / len(per_fill) * 100 if per_fill else 0.0
            print(f"{sym} {label}: {fills} fills, {len(per_fill)} completed (filled+held+exited) cycles over "
                  f"{len(rows)} ticks; mean return per completed cycle = {mean_fill:+.4f}%")


if __name__ == "__main__":
    sys.exit(main())
