"""Run the share-leg backtest for one symbol, with its coin-flip control.

Two runs, always, and never one without the other. The first uses the trading
rule. The second keeps every other part of the machinery identical -- the same
fill convention, the same stop, the same target, the same exits, the same
number of trades -- and replaces only *when* to enter with a coin flip.

The reason to insist on the pair is that a strategy's result is meaningless on
its own. Buying SPY at random moments in a rising market makes money. If the
rule earns 0.03% a trade and random entries earn 0.03% a trade, the rule is
contributing nothing and the profit belongs to the market. Only the difference
between the two lines says anything about the rule.

**The holdout is sealed and this script enforces it.** The share-level
development window was fixed in advance at 2021-07-01 to 2024-12-31. Everything
after it is the final exam, and the point of a final exam is that you sit it
once, on a candidate you have already committed to. Passing --unseal is how you
open it, and the flag exists so that opening it is a deliberate act that shows
up in the shell history rather than a default nobody noticed.

Usage:
    python scripts/run_backtest.py                      # SPY, development window
    python scripts/run_backtest.py --symbol QQQ --feed iex
    python scripts/run_backtest.py --cost 0.0005        # the three-costs report
"""

from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from agent import backtest  # noqa: E402
from agent.params import Config  # noqa: E402

DATA = os.path.join(ROOT, "data")
RESULTS = os.path.join(ROOT, "results", "backtest")

# The share-level split, fixed before any result was read.
DEV_START = "2021-07-01"
DEV_END = "2024-12-31"
FILE_START, FILE_END = "2021-07-01", "2026-08-27"


def describe(name, summary):
    """One block of plain English per run. The files carry the detail."""
    trades = summary["trades"]
    print("  %-8s %6d signals  %6d trades  %5.1f%% won  (break-even needs %.1f%%)"
          % (name, summary["signals"], trades,
             100.0 * summary["win_rate"] if trades else float("nan"),
             100.0 * summary["break_even_win_rate"]))
    if trades:
        print("           mean %+.4f%% per trade   total %+.2f%%   %d ambiguous exits"
              % (100.0 * summary["mean_net_return"],
                 100.0 * summary["total_net_return"],
                 summary["ambiguous_exit_bars"]))
        print("           exits: %s" % ", ".join(
            "%s %d" % (k, v) for k, v in sorted(summary["exit_reasons"].items())))
    if summary["underpowered"]:
        print("           UNDERPOWERED: %d trades against a floor of %d. No profit"
              % (trades, summary["power_floor"]))
        print("           figure from this run means anything, in either direction.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="SPY")
    parser.add_argument("--feed", default="sip", choices=["sip", "iex"])
    parser.add_argument("--start", default=DEV_START)
    parser.add_argument("--end", default=DEV_END)
    parser.add_argument("--cost", type=float, default=0.0,
                        help="cost per side as a fraction of price; 0 is the ceiling")
    parser.add_argument("--seed", type=int, default=20260828)
    parser.add_argument("--unseal", action="store_true",
                        help="permit an end date past the development window")
    parser.add_argument("--no-bar-file", action="store_true",
                        help="skip the per-minute file (large, and the audit trail)")
    args = parser.parse_args()

    if args.end > DEV_END and not args.unseal:
        raise SystemExit(
            "refusing to read past %s -- that is the sealed holdout.\n"
            "Pass --unseal if you really mean to open it, once, on a chosen candidate."
            % DEV_END)

    path = os.path.join(DATA, "%s_1min_%s_%s_%s.csv"
                        % (args.symbol, args.feed, FILE_START, FILE_END))
    bars, data_hash = backtest.load_bars(path, args.start, args.end)
    config = Config(underlying=args.symbol, feed=args.feed)

    print("%s on %s, %s to %s: %s bars over %d sessions"
          % (args.symbol, args.feed, bars[0].session, bars[-1].session,
             "{:,}".format(len(bars)), len({b.session for b in bars})))
    print("settings %s, data %s, cost %.4f%% per side"
          % (config.params_hash(), data_hash, 100.0 * args.cost))
    print()

    stamp = {"data_hash": data_hash, "symbol": args.symbol,
             "window_start": args.start, "window_end": args.end}

    rule = backtest.run(bars, config, entry_mode="rule",
                        cost_fraction_per_side=args.cost,
                        record_bars=not args.no_bar_file)
    describe("rule", rule.summary)

    probability = backtest.control_probability(rule.summary)
    control = backtest.run(bars, config, entry_mode="random",
                           entry_probability=probability, seed=args.seed,
                           cost_fraction_per_side=args.cost, record_bars=False)
    print()
    describe("random", control.summary)

    stem = "%s_%s_%s_%s" % (args.symbol, args.feed, args.start, args.end)
    if args.cost:
        stem += "_cost%g" % args.cost
    written = backtest.write(rule, RESULTS, stem, extra=stamp)
    backtest.write(control, RESULTS, stem + "_control",
                   extra=dict(stamp, control_of=stem))

    print()
    if rule.summary["trades"] and control.summary["trades"]:
        edge = rule.summary["mean_net_return"] - control.summary["mean_net_return"]
        print("The rule beats coin-flip entries by %+.4f%% per trade."
              % (100.0 * edge))
        print("That difference, not the rule's own number, is the whole claim.")
    for name in sorted(written):
        print("  %s" % written[name])
    return 0


if __name__ == "__main__":
    sys.exit(main())
