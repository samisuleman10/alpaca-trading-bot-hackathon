"""Which SPY option contracts have ever existed, and when.

An **option contract** is a right to buy (a *call*) or sell (a *put*) 100
shares at a fixed price -- the *strike* -- up to a fixed date, the *expiry*.
So "SPY 2025-06-20 600 call" is one contract, and "SPY 2025-06-20 601 call" is
a completely different one. SPY has hundreds of live contracts on any given
day and a fresh batch expiring every trading day.

This script downloads the list of them. Not prices -- just what existed.

**Why ask, rather than work it out.** Contract symbols follow a strict format
(`SPY250620C00600000` is SPY, 2025-06-20, Call, strike 600.000), so we could
manufacture the name of any contract we wanted without asking anybody. The
reason not to is that manufacturing a name does not make the contract real.
Strike spacing is not uniform -- close to the current price they sit a dollar
apart, further out five or ten, and monthly expiries carry strikes the daily
ones never had. Ask for bars on a contract we invented and the answer is an
empty list, which is indistinguishable from a contract that existed and simply
never traded.

Those two are not the same thing, and the difference decides whether a skipped
backtest trade is honest. "We wanted the 600 call and nobody traded one" is a
real market condition the live system would also have faced. "We wanted the
600 call and there was no such contract" is our own list being wrong. A
catalogue drawn from the broker's own records tells the two apart.

**Every strike, no filter.** We only ever trade contracts close to the current
price, so most of this download is strikes we will never look at -- SPY had
289 call strikes for 2025-06-20, running from $150 to $830. Keeping all of
them costs a few megabytes, and it means widening the band later is a change
to one line of the backtest rather than a fresh download. It also means the
catalogue is not shaped by what we currently believe the strategy needs, which
is the sort of quiet dependency that is very hard to notice later.

**The calendar decides which days to ask about, not the weekday.** SPY expires
every trading day, and a market holiday simply has no expiry: 2025-06-19 was
Juneteenth and returned zero contracts. Walking weekdays would have recorded
that as a failed request rather than a closed market.

**Resumable.** One file per expiry date, written to a temporary name and only
then moved into place, so a run killed mid-write leaves no half file to be
mistaken for a finished one. Re-running skips everything already present.

Usage:
    python scripts/download_option_contracts.py
    python scripts/download_option_contracts.py --combine-only
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")
CATALOGUE = os.path.join(DATA, "option_contracts")

UNDERLYING = "SPY"

# Option price history begins 2024-01-18 -- earlier expiries have no bars to
# go with them, so cataloguing them would be tidy and useless. The end is the
# last complete session before the build week. Both ends inclusive.
START = "2024-01-18"
END = "2026-08-27"

FIELDS = [
    "symbol", "underlying_symbol", "expiration_date", "type", "style",
    "strike_price", "size", "status", "tradable", "open_interest",
    "open_interest_date", "close_price", "close_price_date", "root_symbol",
]


def alpaca(args, attempts=3):
    """Run the CLI and parse its JSON. Exit code 2 means bad credentials."""
    result = None
    for attempt in range(1, attempts + 1):
        result = subprocess.run(
            ["alpaca"] + args + ["--quiet"], capture_output=True, text=True
        )
        if result.returncode == 0:
            try:
                return json.loads(result.stdout)
            except ValueError:
                pass
        if result.returncode == 2:
            # Not a blip. Retrying a rejected key just rejects it again.
            raise SystemExit("authentication failed -- check ALPACA_PROFILE")
        if attempt < attempts:
            time.sleep(2 * attempt)
    detail = (result.stderr or result.stdout or "").strip()[:300]
    raise RuntimeError("alpaca %s failed: %s" % (" ".join(args), detail))


def sessions(start, end):
    """The days the market was actually open, from the official calendar."""
    days = alpaca(["calendar", "--start", start, "--end", end])
    if isinstance(days, dict):
        days = days.get("calendar") or days.get("data") or []
    return [d["date"] for d in days]


def contracts_expiring_on(day):
    """Every SPY contract with this expiry, paging until the broker stops."""
    rows = []
    token = None
    while True:
        args = [
            "option", "contracts",
            "--underlying-symbols", UNDERLYING,
            "--expiration-date", day,
            "--limit", "10000",
        ]
        # Expired contracts are 'inactive'; the default is active only, which
        # for any past date is an empty list -- a silent, plausible zero. The
        # comparison is against today, not against the end of our window: an
        # expiry one day before the window's end is still an expired contract,
        # and asking for it as 'active' returns nothing at all. That is exactly
        # what happened on the first run, and a zero is the one answer that
        # looks like a legitimate result while being a bug.
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        args += ["--status", "inactive" if day < today else "active"]
        if token:
            args += ["--page-token", token]
        payload = alpaca(args)
        rows.extend(payload.get("option_contracts") or [])
        token = payload.get("next_page_token")
        if not token:
            return rows


def write_day(day, rows):
    path = os.path.join(CATALOGUE, "%s_%s.csv" % (UNDERLYING, day))
    temp = path + ".partial"
    with io.open(temp, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    os.replace(temp, path)  # only now does the file count as finished


def combine():
    """One catalogue file, so nothing downstream has to know about 600 files."""
    out = os.path.join(DATA, "%s_option_contracts.csv" % UNDERLYING.lower())
    names = sorted(n for n in os.listdir(CATALOGUE) if n.endswith(".csv"))
    total = 0
    with io.open(out, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        for name in names:
            with io.open(os.path.join(CATALOGUE, name), encoding="utf-8", newline="") as src:
                for row in csv.DictReader(src):
                    writer.writerow(row)
                    total += 1
    print("combined %d expiries, %s contracts -> %s"
          % (len(names), "{:,}".format(total), out))
    return total


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--combine-only", action="store_true",
                        help="skip downloading; just rebuild the single file")
    args = parser.parse_args()

    os.makedirs(CATALOGUE, exist_ok=True)
    if args.combine_only:
        combine()
        return 0

    days = sessions(START, END)
    print("%d trading days between %s and %s (both inclusive)"
          % (len(days), START, END), flush=True)

    empty = []
    for i, day in enumerate(days, 1):
        path = os.path.join(CATALOGUE, "%s_%s.csv" % (UNDERLYING, day))
        if os.path.exists(path):
            continue
        rows = contracts_expiring_on(day)
        write_day(day, rows)
        if not rows:
            empty.append(day)
        if i % 25 == 0 or not rows:
            print("  %4d/%d  %s  %5d contracts" % (i, len(days), day, len(rows)),
                  flush=True)

    if empty:
        # A trading day with no expiry of its own is possible and is recorded
        # rather than ignored, because the same empty answer is also what a
        # broken request looks like.
        print("\n%d trading days had no expiry of their own: %s"
              % (len(empty), ", ".join(empty[:10]) + (" ..." if len(empty) > 10 else "")))

    combine()
    return 0


if __name__ == "__main__":
    sys.exit(main())
