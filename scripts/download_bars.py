"""Download 1-minute stock bars from Alpaca through the authenticated CLI.

Why the CLI and not the REST API directly: the CLI already holds a logged-in
profile, so no API keys need to live in this process or in the environment.

Two flags here are load-bearing and must not be changed casually:

  --adjustment split  Prices are corrected for stock splits. Without this a
                      10-for-1 split reads as a 90 percent overnight crash and
                      every backtest crossing that date is garbage.
  --feed sip          The consolidated tape: every US exchange. This is the
                      only feed with full history. Note that live trading on
                      the free tier can only use IEX, which sees roughly two
                      percent of the volume, so anything validated here must
                      be re-checked on IEX before it is deployed.

Bars arrive stamped in UTC. Regular US trading hours are 09:30 to 16:00 in
New York, which is UTC-5 or UTC-4 depending on daylight saving, so the filter
below converts properly rather than subtracting a fixed number of hours.
"""

import csv
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")

# Session boundaries come from the official market calendar, not from a fixed
# 09:30-16:00 window. Measured on SPY in 2024: on the three half days (3 July,
# 29 November, 24 December) the market closed at 13:00, but the feed kept
# emitting bars until 15:59 from after-hours trading. A fixed window admits
# those as if they were regular hours, which corrupts the daily average price
# and the volume comparison on exactly the thinnest days of the year.

COLUMNS = [
    "t_utc",       # bar start, UTC
    "t_et",        # bar start, New York time
    "session",     # trading date in New York, groups bars into sessions
    "open",
    "high",
    "low",
    "close",
    "volume",      # shares traded in this minute
    "trades",      # number of separate trades in this minute
    "vwap",        # average price paid this minute, weighted by size
]


def fetch_page(symbol, start, end, page_token=None):
    """Ask the CLI for up to 10,000 bars. Returns the parsed JSON response."""
    cmd = [
        "alpaca", "data", "bars",
        "--symbol", symbol,
        "--start", start,
        "--end", end,
        "--timeframe", "1Min",
        "--feed", "sip",
        "--adjustment", "split",
        "--limit", "10000",
        "--quiet",
    ]
    if page_token:
        cmd += ["--page-token", page_token]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            "alpaca CLI failed for {} ({}): {}".format(
                symbol, result.returncode, result.stderr.strip()[:500]
            )
        )
    if not result.stdout.strip():
        raise RuntimeError("alpaca CLI returned nothing for {}".format(symbol))
    return json.loads(result.stdout)


def fetch_calendar(start, end):
    """Official open and close for every trading day, as minutes past midnight.

    Returns {"2024-12-24": (570, 780), ...}. A date absent from this map is not
    a trading day at all, so any bar stamped on it is dropped.
    """
    result = subprocess.run(
        ["alpaca", "calendar", "--start", start, "--end", end, "--quiet"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "alpaca calendar failed: {}".format(result.stderr.strip()[:500])
        )

    sessions = {}
    for day in json.loads(result.stdout):
        open_h, open_m = day["open"].split(":")
        close_h, close_m = day["close"].split(":")
        sessions[day["date"]] = (
            int(open_h) * 60 + int(open_m),
            int(close_h) * 60 + int(close_m),
        )
    if not sessions:
        raise RuntimeError("calendar returned no trading days")
    return sessions


def in_regular_hours(moment_et, sessions):
    """A bar stamped 15:59 covers 15:59 to 16:00, so the close is exclusive."""
    window = sessions.get(moment_et.strftime("%Y-%m-%d"))
    if window is None:
        return False
    minutes = moment_et.hour * 60 + moment_et.minute
    return window[0] <= minutes < window[1]


def download(symbol, start, end, out_path):
    """Page through the whole range, keep regular-hours bars, write one CSV."""
    sessions = fetch_calendar(start, end)
    half_days = sorted(d for d, w in sessions.items() if w[1] != 16 * 60)
    print("  calendar: {} trading days, {} early closes{}".format(
        len(sessions),
        len(half_days),
        (" (" + ", ".join(half_days) + ")") if half_days else "",
    ))

    kept = 0
    seen = 0
    pages = 0
    page_token = None

    with open(out_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)

        while True:
            payload = fetch_page(symbol, start, end, page_token)
            bars = payload.get("bars") or []
            pages += 1

            for bar in bars:
                seen += 1
                stamp_utc = datetime.strptime(
                    bar["t"], "%Y-%m-%dT%H:%M:%SZ"
                ).replace(tzinfo=timezone.utc)
                stamp_et = stamp_utc.astimezone(NEW_YORK)
                if not in_regular_hours(stamp_et, sessions):
                    continue
                writer.writerow([
                    stamp_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    stamp_et.strftime("%Y-%m-%dT%H:%M:%S"),
                    stamp_et.strftime("%Y-%m-%d"),
                    bar["o"], bar["h"], bar["l"], bar["c"],
                    bar["v"], bar["n"], bar["vw"],
                ])
                kept += 1

            sys.stderr.write(
                "\r  {} page {:>3}  fetched {:>8,}  kept {:>8,}".format(
                    symbol, pages, seen, kept
                )
            )
            sys.stderr.flush()

            page_token = payload.get("next_page_token")
            if not page_token:
                break

    sys.stderr.write("\n")
    return kept, seen, pages


def main():
    if len(sys.argv) != 4:
        print("usage: download_bars.py SYMBOL START END")
        print("  dates are inclusive-start, exclusive-end, e.g. 2024-01-01 2025-01-01")
        return 1

    symbol, start, end = sys.argv[1], sys.argv[2], sys.argv[3]
    here = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(os.path.dirname(here), "data")
    os.makedirs(data_dir, exist_ok=True)
    out_path = os.path.join(
        data_dir, "{}_1min_sip_{}_{}.csv".format(symbol, start, end)
    )

    print("{}  {} to {}  ->  {}".format(symbol, start, end, out_path))
    kept, seen, pages = download(symbol, start, end, out_path)

    size_mb = os.path.getsize(out_path) / (1024 * 1024)
    print(
        "done: {:,} regular-hours bars kept of {:,} fetched, "
        "{} pages, {:.1f} MB".format(kept, seen, pages, size_mb)
    )
    if kept == 0:
        print("WARNING: no bars kept. Check the dates and the symbol.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
