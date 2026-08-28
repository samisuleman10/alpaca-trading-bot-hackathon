"""Sweep the rule's thresholds and find out whether any setting works.

The first run of the share leg lost money: 1,175 trades, 48.2% won against a
55.6% break-even bar, and it did not beat coin-flip entries. But 1,034 of those
1,175 trades ended because a fifteen-minute timer expired, not because the
price did anything. Fifteen minutes was a guess written into the spec before
any data was read, and so were the other thresholds. A rule that fails on one
guessed setting has not told us the idea is wrong -- it has told us that
setting is wrong.

So this runs the same machinery across a grid of settings and asks the
question once per cell.

**Every cell is judged against its own coin flip.** A setting that makes money
is worthless if random entries with the same stop, the same target and the same
exits make just as much; that is the market moving, not the rule working. The
number reported for each cell is the *difference*, rule minus control.

## The reading rule, fixed here before the grid was run

Sweeping 144 settings and reporting the best one is not research, it is
shopping. If you ask 144 questions of pure noise, roughly seven of them come
back at the conventional 5% threshold by luck alone. So the rule for reading
this table is committed in advance, in this docstring, in the commit that first
ran it:

1. **A cell with fewer than 150 trades is not read at all.** Not as a positive,
   not as a negative. Below that count the measurement is too fuzzy to resolve
   the effects we care about, and the honest word is "underpowered".

2. **Significance is corrected for having asked 144 questions.** The correction
   is Benjamini-Hochberg: sort every cell's p-value, and walk down the list
   letting the bar get stricter the further you go. It permits at most 10% of
   whatever we end up calling a discovery to be a fluke. A raw p-value under
   0.05 in a grid this size means nothing on its own.

3. **The winner is never the best cell.** A single outstanding cell surrounded
   by bad ones is noise wearing a hat -- nothing about a market changes
   discontinuously between a 29-minute and a 30-minute holding time. What
   counts is a *contiguous block*: a run of neighbouring settings that all
   point the same way. The candidate is the cell nearest the middle of the
   largest such block, because the middle of a plateau is the setting most
   likely to survive contact with data it has never seen.

4. **If no block survives, the answer is no.** There is no fallback that
   promotes the best cell anyway.

## What is swept and what is not

Swept: how far below the day's average price to buy, how long to hold, whether
to keep trading into the last twenty minutes, and whether the volume
confirmation is required at all.

Held fixed: the profit target (0.1% below the day's average) and the stop
(0.5% below whatever we paid).

**A correction to an earlier version of this file, which claimed those two
fixed the break-even win rate across the whole grid. They do not.** The target
is pinned to the day's average while the entry depth is swept, so the distance
from entry to target -- the reward -- grows as we buy deeper. Risking 0.5% to
make 0.4% needs 55.6% of trades to win; risking 0.5% to make 1.1% needs only
31.2%. The bar therefore changes down every row of every table below, and each
table now prints it.

This does not damage the comparison the grid exists to make. Both arms of a
cell -- the rule and its coin flip -- face the identical bar, so the difference
between them is still a like-for-like measurement. It does mean a raw win rate
cannot be read across rows, and the tables say so where they show one.

The top row of the grid is a special case of this and is **not read at all**.
At an entry depth of 0.1% the buy threshold and the profit target are the same
price, so the reward is exactly zero and the break-even win rate is exactly
100%. Every such trade opens and closes for nothing. A cell like that can post
a 72% win rate and a positive-looking edge while being incapable of earning
anything, which is precisely how a sweep manufactures a finding.

**Costs are zero here, deliberately.** That is the ceiling: the best the rule
could possibly do if trading were free. It is not free, and the real trade is
an option contract whose buy/sell gap is far wider than a share's. A setting
that cannot clear zero cannot clear anything, so the ceiling is the cheapest
possible filter to apply first.

Usage:
    python scripts/run_ablation.py
    python scripts/run_ablation.py --write
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import statistics
import sys
from dataclasses import replace

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from agent import backtest  # noqa: E402
from agent.params import Config  # noqa: E402

DATA = os.path.join(ROOT, "data")
RESULTS = os.path.join(ROOT, "results")

DEV_START, DEV_END = "2021-07-01", "2024-12-31"
FILE_START, FILE_END = "2021-07-01", "2026-08-27"

# The axes. Each is a guess from the spec plus values either side of it, wide
# enough that a plateau would be visible if one existed.
ENTRY_DISTANCE = [-0.001, -0.002, -0.003, -0.005, -0.008, -0.012]
MAX_HOLD = [5, 15, 30, 60, 120, 240]
NO_NEW_ENTRY_AFTER = ["15:40", "15:55"]
VOLUME_RATIO_MIN = [1.00, 1.20]

FDR_Q = 0.10          # at most 10% of anything we call a discovery may be a fluke
SEED = 20260828


def normal_two_sided_p(t):
    """How surprising a difference this size would be if there were no effect.

    Uses the normal curve rather than the exact t-distribution. With hundreds
    of trades in every cell we are allowed to read, the two agree to more
    decimal places than anything here depends on, and the standard library has
    the normal one. Cells below the trade floor are not read at all, so the
    place where the approximation would matter is the place we never look.
    """
    return math.erfc(abs(t) / math.sqrt(2.0))


def welch(a, b):
    """Compare two sets of trade returns that need not be the same size.

    Returns the gap between their averages, and how many standard errors that
    gap is -- the "t". A t near zero means the two are indistinguishable. Two
    is the usual rule of thumb for "worth a second look", and in a grid this
    size it is nowhere near enough on its own.
    """
    if len(a) < 2 or len(b) < 2:
        return float("nan"), float("nan")
    ma, mb = statistics.mean(a), statistics.mean(b)
    va, vb = statistics.variance(a), statistics.variance(b)
    se = math.sqrt(va / len(a) + vb / len(b))
    if se == 0.0:
        return ma - mb, float("nan")
    return ma - mb, (ma - mb) / se


def cells():
    for after in NO_NEW_ENTRY_AFTER:
        for ratio in VOLUME_RATIO_MIN:
            for entry in ENTRY_DISTANCE:
                for hold in MAX_HOLD:
                    yield after, ratio, entry, hold


def reward_of(entry, exit_distance):
    """How much a winning trade actually stands to make.

    The gap between where we buy and where we aim. Zero or less means there is
    nothing to win and the cell is not a strategy, whatever its win rate says.
    """
    return exit_distance - entry


def evaluate(bars, base, after, ratio, entry, hold):
    params = replace(base.strategy_params, no_new_entry_after=after,
                     volume_ratio_min=ratio, entry_distance=entry,
                     max_hold_minutes=hold)
    config = replace(base, strategy_params=params)

    rule = backtest.run(bars, config, entry_mode="rule", record_bars=False)
    probability = backtest.control_probability(rule.summary)
    control = backtest.run(bars, config, entry_mode="random",
                           entry_probability=probability, seed=SEED,
                           record_bars=False)

    edge, t = welch([x.net_return for x in rule.trades],
                    [x.net_return for x in control.trades])
    return {
        "no_new_entry_after": after,
        "volume_ratio_min": ratio,
        "entry_distance": entry,
        "max_hold_minutes": hold,
        "params_hash": config.params_hash(),
        "trades": rule.summary["trades"],
        "control_trades": control.summary["trades"],
        "win_rate": rule.summary["win_rate"],
        "break_even_win_rate": rule.summary["break_even_win_rate"],
        "rule_mean": rule.summary["mean_net_return"],
        "control_mean": control.summary["mean_net_return"],
        "edge": edge,
        "t": t,
        "p": normal_two_sided_p(t) if t == t else float("nan"),
        "reward": reward_of(entry, base.strategy_params.exit_distance),
        # Two separate reasons a cell cannot be read, kept apart because they
        # mean different things: too few trades to measure, versus nothing to
        # measure because a win is worth zero.
        "no_reward": reward_of(entry, base.strategy_params.exit_distance) <= 0.0,
        "underpowered": (rule.summary["underpowered"]
                         or reward_of(entry, base.strategy_params.exit_distance) <= 0.0),
        "few_trades": rule.summary["underpowered"],
        "exit_time": rule.summary["exit_reasons"].get("time", 0),
        "exit_target": rule.summary["exit_reasons"].get("target", 0),
        "exit_stop": rule.summary["exit_reasons"].get("stop", 0),
    }


def benjamini_hochberg(rows, q):
    """Which cells survive after accounting for how many questions we asked.

    Sort the p-values smallest first. The i-th of m gets a bar of i/m * q
    rather than a flat q, so the tenth-best result has to clear a much lower
    bar than the best one. Everything up to the last cell that clears its own
    bar is kept. It is the standard correction and it is not optional here:
    without it, seven of 144 cells would look significant on noise alone.
    """
    readable = [r for r in rows if not r["underpowered"] and r["p"] == r["p"]]
    ranked = sorted(readable, key=lambda r: r["p"])
    m = len(ranked)
    survivors = 0
    for i, row in enumerate(ranked, start=1):
        row["bh_bar"] = i / m * q if m else float("nan")
        if row["p"] <= row["bh_bar"]:
            survivors = i
    for i, row in enumerate(ranked, start=1):
        row["survives_fdr"] = i <= survivors
    for row in rows:
        row.setdefault("bh_bar", float("nan"))
        row.setdefault("survives_fdr", False)
    return m, survivors


def largest_block(rows, after, ratio):
    """The biggest run of neighbouring settings that all beat the coin flip.

    Neighbouring means one step along an axis -- adjacent entry depths at the
    same holding time, or adjacent holding times at the same depth. A lone
    good cell has a block size of one, which is the point: it is
    indistinguishable from luck, and this reports it as such.
    """
    grid = {}
    for row in rows:
        if row["no_new_entry_after"] == after and row["volume_ratio_min"] == ratio:
            key = (ENTRY_DISTANCE.index(row["entry_distance"]),
                   MAX_HOLD.index(row["max_hold_minutes"]))
            grid[key] = row

    good = {k for k, r in grid.items()
            if not r["underpowered"] and r["edge"] == r["edge"] and r["edge"] > 0}
    seen, best = set(), []
    for start in good:
        if start in seen:
            continue
        stack, block = [start], []
        seen.add(start)
        while stack:
            x, y = stack.pop()
            block.append((x, y))
            for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if (nx, ny) in good and (nx, ny) not in seen:
                    seen.add((nx, ny))
                    stack.append((nx, ny))
        if len(block) > len(best):
            best = block
    if not best:
        return [], None
    # The centre of the plateau, not its best corner.
    cx = sum(x for x, _ in best) / len(best)
    cy = sum(y for _, y in best) / len(best)
    centre = min(best, key=lambda c: (c[0] - cx) ** 2 + (c[1] - cy) ** 2)
    return best, grid[centre]


def bp(x):
    """Basis points: hundredths of one percent. 1 bp = 0.01%."""
    return "" if x != x else "%+.2f" % (10000.0 * x)


def render(rows, m, survivors, window, data_hash, base_exit=-0.001):
    out = []
    w = out.append
    readable = [r for r in rows if not r["underpowered"]]
    positive = [r for r in readable if r["edge"] == r["edge"] and r["edge"] > 0]
    winners = [r for r in rows if r["survives_fdr"]]

    w("# Does any setting of this rule beat a coin flip?")
    w("")
    w("Generated by `scripts/run_ablation.py` over %s to %s, SPY share prices,"
      % (window[0], window[1]))
    w("data fingerprint `%s`. Trading costs are set to zero, which makes every" % data_hash)
    w("number here a ceiling rather than an estimate.")
    w("")
    w("## The short version")
    w("")
    w("We tried **%d combinations** of four settings: how far below the day's" % len(rows))
    w("average price to buy, how long to hold, whether to keep trading into the")
    w("last twenty minutes, and whether to require heavier-than-usual volume.")
    w("Each one was run twice -- once with the rule deciding when to enter, and")
    w("once with a coin flip deciding, using the identical stop, target and")
    w("exits. The number that matters is the gap between them.")
    w("")
    no_reward = [r for r in rows if r["no_reward"]]
    few = [r for r in rows if r["few_trades"] and not r["no_reward"]]
    w("- **%d of %d** combinations can be read at all. **%d** are excluded"
      % (len(readable), len(rows), len(no_reward)))
    w("  because a winning trade in them is worth exactly nothing -- the buy")
    w("  price and the target are the same price -- and **%d** produced fewer"
      % len(few))
    w("  than 150 trades, below which the measurement is too fuzzy to mean")
    w("  anything in either direction.")
    w("- **%d of those %d** beat their own coin flip by any margin at all."
      % (len(positive), len(readable)))
    w("- **%d survive** once we account for having asked %d questions."
      % (len(winners), m))
    w("")
    if winners:
        w("The surviving settings are listed below with the block they sit in.")
    else:
        w("**Nothing survives.** Not one setting of this rule, at zero trading")
        w("cost, beats entering at random times with the same exits. The idea is")
        w("not merely mistuned.")
    w("")

    w("## How to read the tables")
    w("")
    w("Each cell is **rule minus coin flip, in basis points per trade**. A basis")
    w("point is a hundredth of one percent, so +5 means the rule earned 0.05%")
    w("more per trade than random entry did. Zero means the rule contributed")
    w("nothing and any profit belonged to the market. Cells marked `--` had")
    w("fewer than 150 trades; cells marked `no reward` are the ones where a")
    w("winning trade is worth zero by construction. Neither is read.")
    w("")
    w("Rows are how far below the day's average price we buy. Columns are how")
    w("many minutes we hold before giving up.")
    w("")
    w("Each row also states the share of trades it must win merely to break")
    w("even. That bar is not the same down the column: buying deeper means a")
    w("bigger gap between the entry and the target, so a winner is worth more")
    w("and fewer of them are needed. It is why a raw win rate cannot be")
    w("compared between rows, and why the number in the cells is the gap")
    w("against that row's own coin flip instead.")
    w("")

    for after in NO_NEW_ENTRY_AFTER:
        for ratio in VOLUME_RATIO_MIN:
            w("### Last entry %s, volume filter %s"
              % (after, "off" if ratio <= 1.0 else "at least %.2f x usual" % ratio))
            w("")
            w("| Buy this far below average | %s |"
              % " | ".join("%d min" % h for h in MAX_HOLD))
            w("| --- | %s |" % " | ".join(["---:"] * len(MAX_HOLD)))
            for entry in ENTRY_DISTANCE:
                reward = reward_of(entry, base_exit)
                bar_needed = (0.005 / (0.005 + reward)) if reward > 0 else 1.0
                line = ["%.1f%% (needs %.0f%% wins)"
                        % (100.0 * entry, 100.0 * bar_needed)]
                for hold in MAX_HOLD:
                    row = next(r for r in rows
                               if r["no_new_entry_after"] == after
                               and r["volume_ratio_min"] == ratio
                               and r["entry_distance"] == entry
                               and r["max_hold_minutes"] == hold)
                    if row["no_reward"]:
                        line.append("no reward")
                    elif row["underpowered"]:
                        line.append("-- (%d)" % row["trades"])
                    else:
                        mark = " *" if row["survives_fdr"] else ""
                        line.append("%s (%d)%s" % (bp(row["edge"]), row["trades"], mark))
                w("| %s |" % " | ".join(line))
            w("")
            block, centre = largest_block(rows, after, ratio)
            if centre is None:
                w("No cell in this table beats its coin flip with enough trades to read.")
            else:
                w("Largest connected run of settings that beat the coin flip: **%d cell%s**."
                  % (len(block), "" if len(block) == 1 else "s"))
                if len(block) == 1:
                    w("One isolated cell is what luck looks like. Nothing about a market")
                    w("changes between a 29-minute and a 30-minute hold, so a good")
                    w("setting with bad neighbours is not a finding.")
                else:
                    w("Its middle is buy at %.1f%% below average, hold %d minutes:"
                      % (100.0 * centre["entry_distance"], centre["max_hold_minutes"]))
                    w("%s bp per trade over %d trades, t = %+.2f, p = %.3f."
                      % (bp(centre["edge"]), centre["trades"], centre["t"], centre["p"]))
            w("")

    w("## The correction, and why the raw numbers lie without it")
    w("")
    w("Ask 144 questions of pure noise and about seven come back looking")
    w("significant at the usual 5% threshold. That is not a flaw in the")
    w("threshold, it is what 5% means. The Benjamini-Hochberg correction")
    w("handles it by making the bar stricter the further down the ranked list")
    w("you read: the best result is judged against %.4f, the tenth against"
      % (FDR_Q / m if m else float("nan")))
    w("%.4f, and so on. We allow at most %d%% of whatever we call a discovery"
      % (10.0 * FDR_Q / m if m else float("nan"), int(100 * FDR_Q)))
    w("to be a fluke.")
    w("")
    best = sorted((r for r in rows if not r["underpowered"] and r["p"] == r["p"]),
                  key=lambda r: r["p"])[:8]
    if best:
        w("| Setting | Trades | Edge (bp) | t | p | Bar it had to clear | Survives |")
        w("| --- | ---: | ---: | ---: | ---: | ---: | :--: |")
        for r in best:
            w("| %.1f%% / %d min / last entry %s / volume %s | %d | %s | %+.2f | %.4f | %.4f | %s |"
              % (100.0 * r["entry_distance"], r["max_hold_minutes"],
                 r["no_new_entry_after"],
                 "off" if r["volume_ratio_min"] <= 1.0 else "%.2f" % r["volume_ratio_min"],
                 r["trades"], bp(r["edge"]), r["t"], r["p"], r["bh_bar"],
                 "yes" if r["survives_fdr"] else "no"))
        w("")

    w("## What this means for the decision")
    w("")
    if winners:
        w("Some settings survive. The next step is **not** to adopt them: they")
        w("are still in-sample, chosen from a table we looked at. The sealed")
        w("holdout exists precisely for this moment, and it is opened once, on")
        w("the middle-of-the-block candidate, not on the best cell.")
    else:
        w("1. **This is not a tuning problem.** The rule was not merely set")
        w("   wrong; no setting of it works. Widening the search further would")
        w("   only raise the number of questions asked, which makes the")
        w("   correction stricter, not the rule better.")
        w("")
        w("2. **The ceiling failed, so everything below it fails.** These runs")
        w("   charge nothing to trade. Real trading costs money, and the trade")
        w("   we actually intend -- buying an option contract -- costs far more")
        w("   per round trip than a share does. A rule that cannot make money")
        w("   for free cannot make money at a price.")
        w("")
        w("3. **The sealed holdout stays sealed.** There is no candidate to test")
        w("   on it. Opening it now would spend the one measurement we have left")
        w("   on a rule we already know does not work.")
        w("")
        w("4. **What this does not say.** It says nothing about whether SPY")
        w("   reverts to its daily average over hours or days -- only that it")
        w("   does not do so within four hours in a way this entry rule can")
        w("   catch. And it says nothing about the option layer's own")
        w("   behaviour, which is a separate question we have not asked.")
    return "\n".join(out) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="SPY")
    parser.add_argument("--feed", default="sip", choices=["sip", "iex"])
    parser.add_argument("--start", default=DEV_START)
    parser.add_argument("--end", default=DEV_END)
    parser.add_argument("--unseal", action="store_true")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    if args.end > DEV_END and not args.unseal:
        raise SystemExit("refusing to read past %s -- that is the sealed holdout."
                         % DEV_END)

    path = os.path.join(DATA, "%s_1min_%s_%s_%s.csv"
                        % (args.symbol, args.feed, FILE_START, FILE_END))
    bars, data_hash = backtest.load_bars(path, args.start, args.end)
    base = Config(underlying=args.symbol, feed=args.feed)
    print("%s %s: %s bars, %s to %s, data %s"
          % (args.symbol, args.feed, "{:,}".format(len(bars)),
             bars[0].session, bars[-1].session, data_hash))

    grid = list(cells())
    rows = []
    for n, (after, ratio, entry, hold) in enumerate(grid, start=1):
        row = evaluate(bars, base, after, ratio, entry, hold)
        rows.append(row)
        print("%3d/%d  %.1f%% / %3d min / %s / vol %.2f  ->  %5d trades  edge %s bp"
              % (n, len(grid), 100.0 * entry, hold, after, ratio,
                 row["trades"], bp(row["edge"])))

    m, survivors = benjamini_hochberg(rows, FDR_Q)
    report = render(rows, m, survivors, (args.start, args.end), data_hash,
                    base.strategy_params.exit_distance)

    if args.write:
        out_dir = os.path.join(RESULTS, "ablation")
        os.makedirs(out_dir, exist_ok=True)
        stem = "%s_%s_%s_%s" % (args.symbol, args.feed, args.start, args.end)
        with io.open(os.path.join(out_dir, stem + ".csv"), "w",
                     encoding="utf-8", newline="\n") as handle:
            writer = csv.DictWriter(handle, fieldnames=sorted(rows[0]))
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
        with io.open(os.path.join(out_dir, stem + ".md"), "w",
                     encoding="utf-8", newline="\n") as handle:
            handle.write(report)
        with io.open(os.path.join(out_dir, stem + "_meta.json"), "w",
                     encoding="utf-8", newline="\n") as handle:
            json.dump({"data_hash": data_hash, "window": [args.start, args.end],
                       "cells": len(rows), "readable": m, "survivors": survivors,
                       "fdr_q": FDR_Q, "seed": SEED, "cost_fraction_per_side": 0.0,
                       "entry_distance": ENTRY_DISTANCE, "max_hold": MAX_HOLD,
                       "no_new_entry_after": NO_NEW_ENTRY_AFTER,
                       "volume_ratio_min": VOLUME_RATIO_MIN},
                      handle, indent=2, sort_keys=True)
        print("wrote %s" % os.path.join(out_dir, stem + ".md"))
    else:
        print()
        print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
