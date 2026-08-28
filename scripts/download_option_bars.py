"""Minute-by-minute prices for the SPY option contracts we could actually trade.

The share downloader had one symbol and a date range. This one has neither,
and that is the whole difficulty. Options expire, so the list of contracts is
different every single day, and there are far too many of them to take all: on
2025-06-20 SPY alone had 578 contracts, with strikes from $150 to $830. Over
two and a half years that is hundreds of thousands of contracts and vastly
more data than the week allows.

So the download is *selective*, and every rule of the selection is written
here rather than discovered later from the shape of the files.

--------------------------------------------------------------------------
What gets downloaded, and why exactly this
--------------------------------------------------------------------------
For each trading session, the contracts that the strategy could plausibly ask
for during that session:

**Expiries within a few trading sessions** (`MAX_SESSIONS_TO_EXPIRY`
below). The rule opens and closes inside a single day, so a contract expiring months out is priced mostly by things that
have nothing to do with today. `target_days_to_expiry` is a swept parameter,
so we need a few values of it to sweep over, not one. Zero sessions ahead
means it expires today -- a "0DTE" contract, the cheapest and the most
violently sensitive to the share price.

**Strikes within a few dollars (`STRIKE_PAD`) of everywhere the share went
that day.** The strike is the price the contract lets you buy at, and it is chosen relative to where SPY is
trading at the moment we decide. SPY moves a few dollars during a session, so
a band fixed on the opening price would be missing the relevant contracts by
the afternoon. The band therefore spans the session's whole low-to-high range
plus a margin on each side, and the margin has to be at least as wide as the
largest `strike_offset` the sweep will ever try.

**Calls and puts both.** The rule as written only ever bets the price goes up,
which needs calls. Puts are downloaded anyway because they cost little here
and because a rule that can only be tested in one direction cannot be checked
against its own mirror image -- and finding that out on Sunday, with no data,
would be finding it out too late.

--------------------------------------------------------------------------
Using the day's high and low to choose the band is not look-ahead
--------------------------------------------------------------------------
It reads like cheating and it is worth being precise about why it is not.

Look-ahead is a *rule* seeing the future. Nothing here is a rule. This decides
which files sit on the shelf; the strategy never sees the shelf, and it never
sees this script. When the strategy asks for a strike, it asks based on the
price at that minute and nothing else.

The one way this could turn into cheating is if a missing contract were
silently skipped -- because "the contracts we happen to have" would then
quietly become part of the entry condition, and it would be a part correlated
with the day's range. So the backtest must **record every contract it asked
for and did not find**, and report the count. A skip that is counted is a
measurement. A skip that is swallowed is a lie with no author.

--------------------------------------------------------------------------
Option bars are sparse, and this matters more than it does for shares
--------------------------------------------------------------------------
Measured on 2024-01-18: the SPY 470 call expiring the next day produced 162
minute bars in a 390-minute session. Not a thin day -- a near-the-money
contract on a normal session. Most minutes, nobody trades that contract at all,
and a minute with no trade produces no bar rather than a flat one.

This is the same hole problem the two share feeds have, and it lands in a
worse place: it means that at the moment the rule fires there may be no
recent price for the contract it wants. The backtest has to treat that as a
missed trade and count it, exactly as with a missing contract, because live
the order would go into a market nobody was quoting.

--------------------------------------------------------------------------
The sealed holdout
--------------------------------------------------------------------------
Development is 2024-01-18 to 2025-08-31. The holdout is 2025-09-01 to
2026-08-27 and is opened exactly once, at the decision gate. Both ends of
every range in this file are inclusive -- the share downloader's usage text
originally claimed otherwise, and that off-by-one is precisely how one day of
a sealed holdout leaks into the development set.

Downloading the holdout now is deliberate and is not the same as looking at
it. Nothing reads those files until the gate, and re-downloading a year of
data on Monday morning is a risk with no upside.

**Resumable.** One file per session, written under a temporary name and moved
into place only when complete, so a run killed mid-write cannot leave a short
file that looks finished.

Usage:
    python scripts/download_option_bars.py
    python scripts/download_option_bars.py --start 2024-01-18 --end 2024-03-31
    python scripts/download_option_bars.py --report
"""

from __future__ import annotations

import argparse
import csv
import io
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")
OUT = os.path.join(DATA, "option_bars")

sys.path.insert(0, HERE)
# Reused rather than copied. The calendar handling is subtle -- half days, and
# a feed that keeps emitting bars after the close -- and a second copy of it
# would drift away from the first without anybody noticing.
from download_bars import fetch_calendar, in_regular_hours, NEW_YORK  # noqa: E402
from download_option_contracts import alpaca  # noqa: E402

UNDERLYING = "SPY"
START = "2024-01-18"
END = "2026-08-27"

# How far ahead an expiry may be, counted in trading sessions rather than
# calendar days. Sessions are the right unit: a Friday contract is one session
# from Thursday and three calendar days from the following Monday, and it is
# the trading time that decides how the price behaves.
MAX_SESSIONS_TO_EXPIRY = 3

# Dollars of strike beyond the session's own low and high. Must be at least
# the largest strike_offset the sweep will try; the check below enforces it.
STRIKE_PAD = 3

# The most symbols one request accepts, per the CLI's own documentation.
BATCH = 100

SHARE_BARS = os.path.join(DATA, "%s_1min_sip_2021-07-01_2026-08-27.csv" % UNDERLYING)

COLUMNS = [
    "t_utc",      # bar start, UTC
    "t_et",       # bar start, New York time
    "session",    # the trading date the bar belongs to
    "symbol",     # the contract, e.g. SPY240119C00470000
    "expiry",     # the date the contract expires
    "right",      # call or put
    "strike",     # the price the contract lets you trade the shares at
    "sessions_to_expiry",  # 0 means it expires today
    "open",
    "high",
    "low",
    "close",
    "volume",     # contracts traded this minute (each covers 100 shares)
    "trades",
    "vwap",
]


def _weeks_after(day, weeks):
    """A date `weeks` weeks later, as a string. Only ever used to look ahead."""
    return (datetime.strptime(day, "%Y-%m-%d")
            + timedelta(weeks=weeks)).strftime("%Y-%m-%d")


def share_session_range(path):
    """The low and high SPY reached in each session, from the share bars.

    Used only to decide which strikes to put on the shelf -- see the note at
    the top of this file about why that is not look-ahead.
    """
    if not os.path.exists(path):
        raise SystemExit(
            "missing %s -- run scripts/download_all_bars.py first" % path)
    ranges = {}
    with io.open(path, encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            day = row["session"]
            low, high = float(row["low"]), float(row["high"])
            if day in ranges:
                previous_low, previous_high = ranges[day]
                ranges[day] = (min(previous_low, low), max(previous_high, high))
            else:
                ranges[day] = (low, high)
    return ranges


def load_catalogue(path):
    """Every contract that existed, grouped by the date it expired on."""
    if not os.path.exists(path):
        raise SystemExit(
            "missing %s -- run scripts/download_option_contracts.py first" % path)
    by_expiry = {}
    with io.open(path, encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                strike = float(row["strike_price"])
            except (TypeError, ValueError):
                continue
            by_expiry.setdefault(row["expiration_date"], []).append(
                (row["symbol"], row["type"], strike))
    return by_expiry


def wanted_contracts(day, day_index, trading_days, ranges, by_expiry):
    """The contracts worth having prices for on this session.

    Returns a list of (symbol, expiry, right, strike, sessions_to_expiry).
    """
    span = ranges.get(day)
    if span is None:
        return []
    low = math.floor(span[0]) - STRIKE_PAD
    high = math.ceil(span[1]) + STRIKE_PAD

    chosen = []
    for ahead in range(MAX_SESSIONS_TO_EXPIRY + 1):
        position = day_index + ahead
        if position >= len(trading_days):
            break
        expiry = trading_days[position]
        for symbol, right, strike in by_expiry.get(expiry, []):
            if low <= strike <= high:
                chosen.append((symbol, expiry, right, strike, ahead))
    return chosen


def fetch_bars(symbols, day):
    """Minute bars for up to BATCH contracts on one session, paged."""
    collected = {}
    token = None
    while True:
        args = [
            "data", "option", "bars",
            "--symbols", ",".join(symbols),
            "--start", day,
            "--end", day,
            "--timeframe", "1Min",
            "--limit", "10000",
        ]
        if token:
            args += ["--page-token", token]
        payload = alpaca(args)
        for symbol, bars in (payload.get("bars") or {}).items():
            collected.setdefault(symbol, []).extend(bars)
        token = payload.get("next_page_token")
        if not token:
            return collected


def write_session(day, rows):
    path = os.path.join(OUT, "%s_%s.csv" % (UNDERLYING, day))
    temp = path + ".partial"
    with io.open(temp, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        writer.writerows(rows)
    os.replace(temp, path)
    return path


def download_session(day, day_index, trading_days, ranges, by_expiry, calendar):
    """One session: choose the contracts, fetch, filter to regular hours."""
    wanted = wanted_contracts(day, day_index, trading_days, ranges, by_expiry)
    if not wanted:
        write_session(day, [])
        return 0, 0

    facts = dict((c[0], c[1:]) for c in wanted)
    symbols = sorted(facts)

    rows = []
    for start in range(0, len(symbols), BATCH):
        batch = symbols[start:start + BATCH]
        for symbol, bars in fetch_bars(batch, day).items():
            expiry, right, strike, ahead = facts[symbol]
            for bar in bars:
                stamp = datetime.strptime(
                    bar["t"][:19], "%Y-%m-%dT%H:%M:%S"
                ).replace(tzinfo=timezone.utc)
                moment_et = stamp.astimezone(NEW_YORK)
                # Options trade outside regular hours too, and those prices
                # are not prices the strategy could ever have acted on.
                if not in_regular_hours(moment_et, calendar):
                    continue
                rows.append([
                    bar["t"],
                    moment_et.strftime("%Y-%m-%d %H:%M:%S"),
                    moment_et.strftime("%Y-%m-%d"),
                    symbol, expiry, right, "%g" % strike, ahead,
                    bar["o"], bar["h"], bar["l"], bar["c"],
                    bar["v"], bar.get("n", 0), bar.get("vw", 0.0),
                ])

    rows.sort(key=lambda r: (r[0], r[3]))
    write_session(day, rows)
    return len(symbols), len(rows)


def report():
    """What we ended up with, per month, without opening the holdout's prices."""
    names = sorted(n for n in os.listdir(OUT) if n.endswith(".csv"))
    by_month = {}
    for name in names:
        day = name[len(UNDERLYING) + 1:-4]
        path = os.path.join(OUT, name)
        with io.open(path, encoding="utf-8") as handle:
            count = max(sum(1 for _ in handle) - 1, 0)
        month = day[:7]
        got = by_month.setdefault(month, [0, 0])
        got[0] += 1
        got[1] += count
    print("%-9s %8s %14s" % ("month", "sessions", "bars"))
    for month in sorted(by_month):
        days, bars = by_month[month]
        print("%-9s %8d %14s" % (month, days, "{:,}".format(bars)))
    total = sum(v[1] for v in by_month.values())
    print("%-9s %8d %14s" % ("total", len(names), "{:,}".format(total)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default=START)
    parser.add_argument("--end", default=END)
    parser.add_argument("--report", action="store_true",
                        help="summarise what is already downloaded and stop")
    args = parser.parse_args()

    os.makedirs(OUT, exist_ok=True)
    if args.report:
        report()
        return 0

    # A band narrower than the widest strike the sweep will ask for would
    # silently starve part of the grid, and the missing cells would look like
    # a strategy result rather than a download gap.
    sys.path.insert(0, os.path.join(ROOT, "src"))
    from agent.params import ExpressionParams  # noqa: E402
    if abs(ExpressionParams().strike_offset) > STRIKE_PAD:
        raise SystemExit("STRIKE_PAD %d is narrower than the strike offsets in use"
                         % STRIKE_PAD)

    # Ask the calendar for a fortnight beyond the last session we intend to
    # download. The expiry list for a session is built by looking forward, so
    # a calendar that stops on the final day would hand the last few sessions
    # a short list of expiries and every one of them would look like a quiet
    # market rather than a truncated request. The extra days are used only to
    # look ahead; nothing outside [start, end] is downloaded.
    calendar = fetch_calendar(args.start, _weeks_after(args.end, 2))
    trading_days = sorted(calendar)
    downloadable = [d for d in trading_days if args.start <= d <= args.end]
    ranges = share_session_range(SHARE_BARS)
    by_expiry = load_catalogue(
        os.path.join(DATA, "%s_option_contracts.csv" % UNDERLYING.lower()))

    print("%d sessions to download, %d expiries catalogued, expiries up to %d "
          "sessions ahead, strikes within $%d of the day's range"
          % (len(downloadable), len(by_expiry), MAX_SESSIONS_TO_EXPIRY, STRIKE_PAD),
          flush=True)

    started = time.time()
    contracts_total = 0
    bars_total = 0
    for position, day in enumerate(downloadable):
        path = os.path.join(OUT, "%s_%s.csv" % (UNDERLYING, day))
        if os.path.exists(path):
            continue
        contracts, bars = download_session(
            day, trading_days.index(day), trading_days, ranges, by_expiry, calendar)
        contracts_total += contracts
        bars_total += bars
        if position % 10 == 0 or contracts == 0:
            print("  %4d/%d  %s  %3d contracts  %6s bars  (%.0f s elapsed)"
                  % (position + 1, len(downloadable), day, contracts,
                     "{:,}".format(bars), time.time() - started), flush=True)

    print("\ndone: %s contracts asked for, %s bars, %.0f s"
          % ("{:,}".format(contracts_total), "{:,}".format(bars_total),
             time.time() - started))
    return 0


if __name__ == "__main__":
    sys.exit(main())
