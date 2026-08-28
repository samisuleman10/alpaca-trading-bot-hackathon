"""Record live option bid and ask prices to a CSV, once a minute, until the close.

WHY THIS EXISTS, because it is not obvious and the reason is the whole point:

Alpaca publishes no history of option bid and ask prices. Not on a paid tier,
not behind an agreement - the endpoint does not exist (measured 2026-08-28, see
docs/options_data.md). What it does publish is every price at which a contract
actually *traded*.

The gap between the bid (what a buyer is offering) and the ask (what a seller
is demanding) is called the **spread**, and on options it is the single largest
cost of trading. You buy at the ask and sell at the bid, so the spread is money
gone the instant you enter, before the trade is right or wrong about anything.

So the backtest cannot measure its own biggest cost. It has to estimate it. The
only calibration data that will ever exist for that estimate is data we record
ourselves, live, while the market is open - which is why this script matters
today and cannot be caught up later. Every minute the market is open and this
is not running is a minute that is gone.

WHAT IT RECORDS

One row per contract per poll. Both the live quote and the delayed trade, side
by side, deliberately: the delayed trades are what the backtest will actually
see, the live quote is the answer it is trying to predict, and a model is only
testable if both are written down together.

Contracts are the ones near the current SPY price, across the next few expiry
dates, because the spread depends on both - a contract far from the money or
far from expiry is a different animal from one at the money and expiring today.

WHAT IT DOES NOT DO

It never places an order, and it never can: the only Alpaca commands it runs
are `data latest-quote`, `data option chain` and `clock`, all read-only.

WHY THE CLI AND NOT THE REST API

Same reason as download_bars.py: the CLI already holds a logged-in profile, so
no API keys live in this process or in the environment.

FEEDS

The underlying is read from **IEX** rather than the consolidated tape, on
purpose. IEX is a single small exchange seeing roughly two percent of trading,
and it is the only real-time share feed the free tier allows. It is what the
live trader will see, so it is what gets recorded. Options come from **OPRA**,
the consolidated options tape, whose quotes are real-time and free.

USAGE

    python hackathon/scripts/record_option_quotes.py
    python hackathon/scripts/record_option_quotes.py --underlying SPY --minutes 90

Safe to stop and restart: rows are appended and the header written only once.
"""

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")

# How far either side of the current share price to record, in dollars. SPY
# strikes near the money are one dollar apart, so this is roughly 25 contracts
# per expiry per side. Wide enough that the model can learn how the spread
# widens away from the money; narrow enough to stay in one request.
STRIKE_WINDOW = 12.0

# How many expiry dates to follow. The nearest is today's - a "0DTE" contract,
# zero days to expiry - which is the one the strategy intends to trade and also
# the one with the most violent spread behaviour.
EXPIRIES_TO_FOLLOW = 3

# Trading days to scan when discovering which expiry dates exist. SPY lists
# contracts expiring Monday, Wednesday and Friday, so ten calendar days is
# comfortably more than three expiries.
EXPIRY_SEARCH_DAYS = 10

POLL_SECONDS = 60

COLUMNS = [
    "poll_utc",          # our clock when the poll started - the row's identity
    "poll_seq",          # 0, 1, 2 ... lets a whole poll be reconstructed
    "underlying",        # SPY
    "under_bid",         # live, from IEX
    "under_ask",
    "under_ts",          # exchange timestamp of that quote
    "contract",          # e.g. SPY260828C00772000
    "expiry",            # 2026-08-28
    "right",             # call or put
    "strike",
    "moneyness",         # strike minus the underlying mid. 0 is at the money
    "opt_bid",           # live, from OPRA. THE NUMBER THIS SCRIPT EXISTS FOR
    "opt_ask",
    "opt_bid_size",      # how many contracts are on offer at that price
    "opt_ask_size",
    "opt_quote_ts",      # exchange timestamp - expect it to track the clock
    "trade_price",       # last traded price, DELAYED ~15 minutes
    "trade_size",
    "trade_ts",          # expect this to trail poll_utc by about 15 minutes
    "bar_close",         # last minute bar we may see, also delayed
    "bar_high",
    "bar_low",
    "bar_vwap",          # average price paid in that minute, weighted by size
    "bar_volume",
    "bar_trades",        # number of separate trades - a liquidity signal
    "bar_ts",
    "day_volume",        # contracts traded today so far
]


def alpaca(args):
    """Run one read-only alpaca CLI command and return its parsed JSON."""
    exe = shutil.which("alpaca")
    if exe is None:
        raise RuntimeError("the alpaca CLI is not on PATH")
    proc = subprocess.run(
        [exe] + args + ["--quiet"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "alpaca %s failed (%d): %s"
            % (" ".join(args), proc.returncode, proc.stderr.strip()[:300])
        )
    return json.loads(proc.stdout)


def parse_ts(text):
    """Alpaca timestamps are RFC 3339 with a Z and often nanosecond precision,
    which Python's parser refuses. Trim to microseconds and hand it back."""
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    if "." in text:
        head, rest = text.split(".", 1)
        frac, _, tail = rest.partition("+")
        text = "%s.%s+%s" % (head, frac[:6], tail)
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def contract_parts(symbol, underlying):
    """Pull the expiry, call/put and strike back out of an OCC contract symbol.

    The format is fixed width and positional: underlying, then YYMMDD, then a
    single C or P, then the strike in thousandths of a dollar padded to eight
    digits. So SPY260828C00772000 is a SPY call at $772 expiring 28 Aug 2026."""
    tail = symbol[len(underlying):]
    yy, mm, dd = tail[0:2], tail[2:4], tail[4:6]
    right = "call" if tail[6] == "C" else "put"
    strike = int(tail[7:15]) / 1000.0
    return "20%s-%s-%s" % (yy, mm, dd), right, strike


def underlying_mid(symbol):
    """Current price of the shares, as the midpoint of the live IEX quote."""
    payload = alpaca(["data", "latest-quote", "--symbol", symbol, "--feed", "iex"])
    quote = payload["quote"]
    bid, ask = float(quote["bp"]), float(quote["ap"])
    # A zero on one side means that side of the book is empty on this one small
    # exchange, which happens. Fall back to the side that exists rather than
    # halving the price and silently recording a nonsense midpoint.
    if bid <= 0 and ask <= 0:
        raise RuntimeError("no usable %s quote on IEX" % symbol)
    if bid <= 0:
        return ask, bid, ask, quote.get("t")
    if ask <= 0:
        return bid, bid, ask, quote.get("t")
    return (bid + ask) / 2.0, bid, ask, quote.get("t")


def fetch_chain(underlying, expiry, low, high):
    """One request: every contract for one expiry within a strike band."""
    payload = alpaca([
        "data", "option", "chain",
        "--underlying-symbol", underlying,
        "--expiration-date", expiry,
        "--strike-price-gte", "%.2f" % low,
        "--strike-price-lte", "%.2f" % high,
        "--limit", "500",
    ])
    return payload.get("snapshots") or {}


def discover_expiries(underlying, mid, count):
    """Ask which of the next few dates actually have contracts listed.

    Done by asking rather than by assuming a Monday/Wednesday/Friday pattern,
    because holidays move expiries and a wrong assumption here would silently
    record nothing."""
    today = datetime.now(NEW_YORK).date()
    found = []
    for offset in range(EXPIRY_SEARCH_DAYS):
        day = (today + timedelta(days=offset)).isoformat()
        try:
            # A one dollar band is enough to prove the date exists, and keeps
            # this startup scan cheap.
            if fetch_chain(underlying, day, mid - 1, mid + 1):
                found.append(day)
        except RuntimeError as exc:
            print("  expiry scan %s: %s" % (day, exc), file=sys.stderr)
        if len(found) == count:
            break
    return found


def rows_for_poll(underlying, expiries, seq):
    poll_utc = datetime.now(timezone.utc)
    mid, under_bid, under_ask, under_ts = underlying_mid(underlying)
    rows = []
    for expiry in expiries:
        snapshots = fetch_chain(
            underlying, expiry, mid - STRIKE_WINDOW, mid + STRIKE_WINDOW
        )
        for symbol, snap in sorted(snapshots.items()):
            quote = snap.get("latestQuote") or {}
            # No live quote means nothing worth recording: the whole purpose of
            # the row is the bid and the ask.
            if not quote:
                continue
            trade = snap.get("latestTrade") or {}
            bar = snap.get("minuteBar") or {}
            daily = snap.get("dailyBar") or {}
            exp, right, strike = contract_parts(symbol, underlying)
            rows.append({
                "poll_utc": poll_utc.isoformat(timespec="seconds"),
                "poll_seq": seq,
                "underlying": underlying,
                "under_bid": under_bid,
                "under_ask": under_ask,
                "under_ts": under_ts,
                "contract": symbol,
                "expiry": exp,
                "right": right,
                "strike": strike,
                "moneyness": round(strike - mid, 2),
                "opt_bid": quote.get("bp"),
                "opt_ask": quote.get("ap"),
                "opt_bid_size": quote.get("bs"),
                "opt_ask_size": quote.get("as"),
                "opt_quote_ts": quote.get("t"),
                "trade_price": trade.get("p"),
                "trade_size": trade.get("s"),
                "trade_ts": trade.get("t"),
                "bar_close": bar.get("c"),
                "bar_high": bar.get("h"),
                "bar_low": bar.get("l"),
                "bar_vwap": bar.get("vw"),
                "bar_volume": bar.get("v"),
                "bar_trades": bar.get("n"),
                "bar_ts": bar.get("t"),
                "day_volume": daily.get("v"),
            })
    return rows, mid, poll_utc


def freshness_note(rows, poll_utc):
    """One line per poll saying how old the two kinds of data are.

    This is not decoration. The claim that quotes are live and trades are
    delayed by fifteen minutes is load-bearing for the whole design, and this
    reprints the evidence for it every single minute the script runs."""
    quote_lag = trade_lag = None
    for row in rows:
        q = parse_ts(row["opt_quote_ts"])
        t = parse_ts(row["trade_ts"])
        if q is not None:
            lag = (poll_utc - q).total_seconds()
            quote_lag = lag if quote_lag is None else min(quote_lag, lag)
        if t is not None:
            lag = (poll_utc - t).total_seconds()
            trade_lag = lag if trade_lag is None else min(trade_lag, lag)
    def fmt(value):
        return "n/a" if value is None else "%.0fs" % value
    return "quote lag %s, trade lag %s" % (fmt(quote_lag), fmt(trade_lag))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--underlying", default="SPY")
    parser.add_argument(
        "--minutes", type=int, default=None,
        help="stop after this many minutes; default is to run until the close",
    )
    parser.add_argument(
        "--out-dir", default=os.path.join("hackathon", "data", "option_quotes"),
    )
    args = parser.parse_args()

    clock = alpaca(["clock"])
    if not clock.get("is_open"):
        print(
            "The market is shut. Next open %s. Nothing to record."
            % clock.get("next_open"),
            file=sys.stderr,
        )
        return 1
    close_at = parse_ts(clock["next_close"])

    # Stop a minute before the bell. The last minute of the session is not
    # worth a half-written row, and a poll that starts at 15:59:58 finishes
    # after the close.
    deadline = close_at - timedelta(minutes=1)
    if args.minutes is not None:
        deadline = min(
            deadline,
            datetime.now(timezone.utc) + timedelta(minutes=args.minutes),
        )

    session = datetime.now(NEW_YORK).date().isoformat()
    os.makedirs(args.out_dir, exist_ok=True)
    path = os.path.join(
        args.out_dir, "%s_%s.csv" % (args.underlying.lower(), session)
    )
    new_file = not os.path.exists(path)

    mid, _, _, _ = underlying_mid(args.underlying)
    print("%s is at %.2f" % (args.underlying, mid))
    expiries = discover_expiries(args.underlying, mid, EXPIRIES_TO_FOLLOW)
    if not expiries:
        print("No expiries found. Nothing to record.", file=sys.stderr)
        return 1
    print("Following expiries: %s" % ", ".join(expiries))
    print("Writing to %s" % path)
    print("Until %s" % deadline.astimezone(NEW_YORK).strftime("%H:%M:%S %Z"))

    handle = open(path, "a", newline="", encoding="utf-8")
    writer = csv.DictWriter(handle, fieldnames=COLUMNS)
    if new_file:
        writer.writeheader()

    seq = 0
    written = 0
    failures = 0
    try:
        while datetime.now(timezone.utc) < deadline:
            started = time.time()
            try:
                rows, mid, poll_utc = rows_for_poll(
                    args.underlying, expiries, seq
                )
                writer.writerows(rows)
                handle.flush()
                written += len(rows)
                failures = 0
                print(
                    "%s  poll %-4d %s %7.2f  %3d rows (%d total)  %s"
                    % (
                        poll_utc.astimezone(NEW_YORK).strftime("%H:%M:%S"),
                        seq, args.underlying, mid, len(rows), written,
                        freshness_note(rows, poll_utc),
                    ),
                    flush=True,
                )
            except Exception as exc:
                # A failed poll is a missing minute, not a reason to lose the
                # rest of the session. Only a run of them means something is
                # actually broken.
                failures += 1
                print("poll %d failed: %s" % (seq, exc), file=sys.stderr,
                      flush=True)
                if failures >= 10:
                    print("ten consecutive failures, stopping",
                          file=sys.stderr)
                    return 1
            seq += 1
            time.sleep(max(0.0, POLL_SECONDS - (time.time() - started)))
    except KeyboardInterrupt:
        print("\nstopped by hand")
    finally:
        handle.close()

    print("%d rows in %s" % (written, path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
