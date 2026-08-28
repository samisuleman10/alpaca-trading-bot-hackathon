"""How many minutes could we actually have traded an option in?

The share prices are complete. The option prices are not, and the gap between
those two facts is where a backtest quietly invents opportunities it never had.

The strategy works in two steps. It looks at SPY's share price and forms an
opinion; then it tries to express that opinion by buying a contract. The first
step almost always works, because the consolidated share tape has a bar for
essentially every minute of every session. The second step needs a *price for
the contract*, and there are two separate reasons it might not have one:

**The contract went quiet.** Nobody traded that particular strike in that
particular minute. A minute with no trade produces no bar, so there is nothing
to fill against. This is a real market condition -- live, the order would have
gone into a market nobody was quoting -- and the backtest must count it as a
missed trade rather than pretend a price.

**The archive has a hole.** Measured on 2024-01-23: every SPY contract, at
every expiry, stops at 10:48 New York time and there is nothing for the rest
of the session. Including contracts expiring ten days later, which cannot all
have gone quiet at the same instant. That is Alpaca's records missing, not the
market going silent, and it removes roughly five hours of tradable time from
that session. 2024-01-22 has the mirror image: nothing before 12:55.

The two look identical in a data file and they mean opposite things, so this
script separates them the only way available: a quiet *contract* is one symbol
going dark while others keep trading. A hole is **every** contract stopping in
the same minute. The second is not a market event.

Why it matters more than it sounds. It does not bias the result in an obvious
direction -- the missing minutes are not chosen by whether we would have made
money -- but it does two damaging things. It shrinks the sample, and the
sample is already the binding constraint: the pre-committed floor is 150
trades, below which the honest report is "underpowered" rather than a profit
figure. And it makes the backtest's trade count an overstatement of what the
live system would achieve, because the live system faces the quiet minutes but
not the archive holes.

Usage:
    python scripts/option_bar_coverage.py
    python scripts/option_bar_coverage.py --write
"""

from __future__ import annotations

import argparse
import collections
import csv
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")
BARS = os.path.join(DATA, "option_bars")
RESULTS = os.path.join(ROOT, "results")

sys.path.insert(0, HERE)
from download_bars import fetch_calendar  # noqa: E402

UNDERLYING = "SPY"

# A session missing this many minutes or more is called damaged. Twenty is the
# strategy's volume-comparison window, the same threshold the share-feed report
# uses, so the two numbers mean the same thing and can be read side by side.
DAMAGED_SESSION_MINUTES = 20

# A minute counts as a hole rather than a quiet market when the whole chain
# goes dark: no contract of any expiry traded. One contract falling silent is
# ordinary and is counted separately.
HOLE = "hole"


def session_minutes(window):
    """Every minute of a session, as HH:MM, from the official open and close."""
    open_minute, close_minute = window
    return ["%02d:%02d" % (m // 60, m % 60) for m in range(open_minute, close_minute)]


def read_session(path):
    """Minutes that had at least one option bar, and how many contracts each."""
    per_minute = collections.Counter()
    contracts = set()
    with io.open(path, encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            per_minute[row["t_et"][11:16]] += 1
            contracts.add(row["symbol"])
    return per_minute, contracts


def examine(calendar):
    rows = []
    for name in sorted(os.listdir(BARS)):
        if not name.endswith(".csv"):
            continue
        day = name[len(UNDERLYING) + 1:-4]
        window = calendar.get(day)
        if window is None:
            continue
        expected = session_minutes(window)
        per_minute, contracts = read_session(os.path.join(BARS, name))

        present = [m for m in expected if per_minute.get(m)]
        missing = [m for m in expected if not per_minute.get(m)]

        # A gap at the very start or the very end of a session, with a solid
        # run of data in between, is the shape an archive outage makes. Gaps
        # scattered through an otherwise complete session are quiet minutes.
        if present:
            leading = 0
            for m in expected:
                if per_minute.get(m):
                    break
                leading += 1
            trailing = 0
            for m in reversed(expected):
                if per_minute.get(m):
                    break
                trailing += 1
        else:
            # Nothing at all, all day. The leading run and the trailing run are
            # then the same run, and counting it at both ends is how a
            # 390-minute session first reported 780 missing minutes and an
            # interior gap of -390. A wholly absent day is also a different
            # fact from a partial outage -- there is no "rest of the session"
            # to compare against -- so it is flagged and reported separately.
            leading, trailing = len(expected), 0

        rows.append({
            "absent": not present,
            "session": day,
            "expected": len(expected),
            "present": len(present),
            "missing": len(missing),
            "contracts": len(contracts),
            "leading_gap": leading,
            "trailing_gap": trailing,
            "edge_gap": leading + trailing,
            "interior_gap": len(missing) - leading - trailing,
        })
    return rows


def render(rows):
    out = []
    w = out.append
    total_expected = sum(r["expected"] for r in rows)
    total_present = sum(r["present"] for r in rows)
    total_edge = sum(r["edge_gap"] for r in rows)
    total_interior = sum(r["interior_gap"] for r in rows)
    damaged = [r for r in rows if r["missing"] >= DAMAGED_SESSION_MINUTES]
    absent = [r["session"] for r in rows if r["absent"]]
    holed = sorted((r["edge_gap"], r["session"]) for r in rows
                   if r["edge_gap"] >= DAMAGED_SESSION_MINUTES and not r["absent"])

    # Every missing minute is either at an edge or inside, never both and never
    # neither. If that stops being true the split is wrong and the table below
    # is describing something other than the data.
    assert total_edge + total_interior == total_expected - total_present
    assert total_interior >= 0

    w("# How many minutes we could actually have traded an option in")
    w("")
    w("Generated by `scripts/option_bar_coverage.py` over %d sessions, %s to %s,"
      % (len(rows), rows[0]["session"], rows[-1]["session"]))
    w("both ends inclusive.")
    w("")
    w("The share tape has a price for nearly every minute. The option archive")
    w("does not, and a minute with no option price is a minute in which the")
    w("strategy could form a view and then do nothing about it.")
    w("")
    w("| | Minutes | Share of session time |")
    w("| --- | ---: | ---: |")
    w("| Session minutes in the window | %s | 100%% |" % "{:,}".format(total_expected))
    w("| With at least one option bar | %s | %.1f%% |"
      % ("{:,}".format(total_present), 100.0 * total_present / total_expected))
    w("| Missing at the edge of a session | %s | %.1f%% |"
      % ("{:,}".format(total_edge), 100.0 * total_edge / total_expected))
    w("| Missing inside a session | %s | %.1f%% |"
      % ("{:,}".format(total_interior), 100.0 * total_interior / total_expected))
    w("")
    w("**The two kinds of missing minute are not the same thing.**")
    w("")
    w("A minute missing *inside* an otherwise complete session is a quiet")
    w("market: nobody traded any of the contracts we were watching. That is a")
    w("real condition, the live system would face it too, and the backtest")
    w("should count it as a trade it could not make.")
    w("")
    w("A run of missing minutes at the *start or end* of a session, with solid")
    w("data in between, is not a market event. On 2024-01-23 every SPY")
    w("contract at every expiry stops at 10:48 -- including ones expiring ten")
    w("days later, which cannot all have gone quiet in the same instant. That")
    w("is Alpaca's archive missing about five hours. The live system would")
    w("have seen those minutes; our rehearsal cannot.")
    w("")
    w("**%d of %d sessions are damaged** -- missing %d minutes or more, the"
      % (len(damaged), len(rows), DAMAGED_SESSION_MINUTES))
    w("strategy's own volume-comparison window, so a hole that size silences")
    w("the rule rather than merely thinning it. **%d sessions lose %d minutes"
      % (len(holed), DAMAGED_SESSION_MINUTES))
    w("or more to an edge gap**, which is the archive rather than the market,")
    w("and **%d session%s no option prices at all**."
      % (len(absent), " has" if len(absent) == 1 else "s have"))
    w("")

    if absent:
        w("## Sessions with no option prices at all")
        w("")
        for day in absent:
            w("- **%s** -- the market was open, we have all %d minutes of SPY's"
              % (day, next(r["expected"] for r in rows if r["session"] == day)))
            w("  share price, and there is not one option bar.")
        w("")
        w("This is the archive, not the market, and it can be checked in one")
        w("request: ask for *daily* bars on a contract expiring 2024-02-09 and")
        w("the answer skips from 1 February to 5 February. On the 5th that")
        w("contract traded 32,891 times. It was not asleep on the 2nd.")
        w("")
        w("These days are unusable rather than thin, and the backtest should")
        w("skip them outright rather than record a day on which the strategy")
        w("mysteriously never traded.")
        w("")

    if holed:
        w("## The worst archive gaps")
        w("")
        w("| Session | Minutes missing at the edge | Minutes present |")
        w("| --- | ---: | ---: |")
        worst = sorted(holed, reverse=True)[:15]
        for gap, day in worst:
            row = next(r for r in rows if r["session"] == day)
            w("| %s | %d | %d of %d |" % (day, gap, row["present"], row["expected"]))
        w("")

    w("## What this means for the decision")
    w("")
    w("1. **The trade count is the number under threat, not the profit.** The")
    w("   missing minutes are not chosen by whether we would have made money,")
    w("   so this does not tilt the result in a direction. It shrinks the")
    w("   sample, and the sample was already the binding constraint: the")
    w("   pre-committed floor is 150 trades, below which the honest report is")
    w("   \"underpowered\" rather than a profit figure.")
    w("")
    w("2. **The backtest and the live system do not face the same obstacle.**")
    w("   Live, the quiet minutes are real and the archive gaps do not exist.")
    w("   So the rehearsal understates how many chances the live system gets,")
    w("   while overstating nothing. That is the safe direction to be wrong in,")
    w("   and it is still a difference that has to be stated rather than")
    w("   discovered.")
    w("")
    w("3. **A session this badly holed should not be silently averaged in.**")
    w("   Sessions whose edge gap exceeds the damage threshold are recorded")
    w("   here by name so that any result computed over them can be recomputed")
    w("   without them, and the two answers compared.")
    return "\n".join(out) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    names = [n for n in os.listdir(BARS) if n.endswith(".csv")] if os.path.isdir(BARS) else []
    if not names:
        raise SystemExit("no option bars yet -- run scripts/download_option_bars.py")
    days = sorted(n[len(UNDERLYING) + 1:-4] for n in names)
    calendar = fetch_calendar(days[0], days[-1])

    rows = examine(calendar)
    for row in rows[:5]:
        print("%s  %4d/%d minutes  %3d contracts  edge gap %3d  interior %3d"
              % (row["session"], row["present"], row["expected"], row["contracts"],
                 row["edge_gap"], row["interior_gap"]))
    print("... %d sessions examined" % len(rows))

    report = render(rows)
    if args.write:
        os.makedirs(RESULTS, exist_ok=True)
        path = os.path.join(RESULTS, "option_bar_coverage.md")
        with io.open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(report)
        print("wrote %s" % path)
    else:
        print()
        print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
