"""Fetch every symbol on every feed, unattended, and be resumable.

This runs for hours. Most of that is waiting on the network, not working, so
the only real requirements are that it survives a dropped connection and that
it can be restarted without redoing what it already has.

**Resumable, at the granularity of one symbol on one feed.** A finished file is
skipped. An unfinished one is not: the downloader writes the CSV as it pages,
so a run killed halfway leaves a file that looks complete and is not. There is
no way to tell the two apart by looking, so every finished download stamps a
small `.done` file next to it recording the row count, and only that stamp
counts as evidence. A CSV without its stamp is thrown away and refetched.

**Twenty jobs, not ten.** Ten symbols on two feeds. SIP is the consolidated
tape -- every US exchange, full history, and what the backtest is measured on.
IEX is one small exchange seeing a few per cent of the volume, and it is what
the free tier lets us trade on live. A rule that works on SIP and not on IEX is
a rule we cannot actually run, so both are downloaded from the start rather
than discovering the problem in September.

**Ten symbols, one traded.** The share-level screen runs on all ten to get some
statistical breadth -- one symbol is one test, and one test proves very little.
Options are downloaded, backtested and traded on SPY alone, because ten
symbols' worth of option chains is a data job we do not have the week for. That
split was decided on 2026-08-28, before any result was read.

Usage:
    python scripts/download_all_bars.py                 # everything
    python scripts/download_all_bars.py --feed iex      # one feed
    python scripts/download_all_bars.py --symbols SPY QQQ
    python scripts/download_all_bars.py --retry 5
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")
LOG = os.path.join(DATA, "download_log.txt")

# The ten the candidate specification names. SPY, QQQ and IWM are funds holding
# baskets of shares -- 500 large US companies, 100 large technology-leaning
# ones, and 2,000 small ones. The other seven are single companies. The mix is
# deliberate: a rule that works on a basket and not on any individual company
# is telling us something different from one that works on both.
SYMBOLS = ["SPY", "QQQ", "IWM", "AAPL", "MSFT", "NVDA", "AMD", "TSLA", "META", "AMZN"]

FEEDS = ["sip", "iex"]

# Both ends inclusive -- measured, not assumed. See download_bars.py.
START = "2021-07-01"
END = "2026-08-27"

PYTHON = sys.executable
DOWNLOADER = os.path.join(HERE, "download_bars.py")


def note(message):
    """Print it and keep it. An unattended run nobody watched needs a record."""
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = "%s  %s" % (stamp, message)
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def paths_for(symbol, feed):
    base = os.path.join(DATA, "%s_1min_%s_%s_%s" % (symbol, feed, START, END))
    return base + ".csv", base + ".done"


def already_have(symbol, feed):
    """True only when both the file and its completion stamp are present."""
    csv_path, done_path = paths_for(symbol, feed)
    return os.path.exists(csv_path) and os.path.exists(done_path)


def count_rows(path):
    with open(path, "r", encoding="utf-8") as handle:
        return sum(1 for _ in handle) - 1  # minus the header


def fetch(symbol, feed, attempts):
    """One symbol, one feed, with retries. Returns True if it ended complete."""
    csv_path, done_path = paths_for(symbol, feed)

    for attempt in range(1, attempts + 1):
        # A leftover CSV from a killed run is not partial data we can build on
        # -- the downloader restarts from page one and rewrites the file. Left
        # in place it would only be mistaken for a finished download.
        if os.path.exists(csv_path):
            os.remove(csv_path)

        note("%s %s: attempt %d of %d" % (symbol, feed, attempt, attempts))
        started = time.time()
        result = subprocess.run(
            [PYTHON, DOWNLOADER, symbol, START, END, feed],
            capture_output=True,
            text=True,
        )
        elapsed = time.time() - started

        if result.returncode == 0 and os.path.exists(csv_path):
            rows = count_rows(csv_path)
            if rows <= 0:
                note("%s %s: file has no rows -- treating as a failure" % (symbol, feed))
                continue
            size_mb = os.path.getsize(csv_path) / (1024 * 1024)
            with open(done_path, "w", encoding="utf-8") as handle:
                handle.write(
                    "symbol=%s\nfeed=%s\nstart=%s\nend=%s\nrows=%d\n"
                    "seconds=%.1f\nfinished_utc=%s\n"
                    % (
                        symbol, feed, START, END, rows, elapsed,
                        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    )
                )
            note(
                "%s %s: done, %s rows, %.1f MB, %.0f s"
                % (symbol, feed, "{:,}".format(rows), size_mb, elapsed)
            )
            return True

        tail = (result.stderr or result.stdout or "").strip().splitlines()
        note(
            "%s %s: failed (exit %d) -- %s"
            % (symbol, feed, result.returncode, tail[-1] if tail else "no output")
        )
        if attempt < attempts:
            # Back off a little further each time. A rate limit or a blip
            # clears on its own; hammering it does not help.
            pause = 15 * attempt
            note("%s %s: waiting %d s" % (symbol, feed, pause))
            time.sleep(pause)

    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="+", default=SYMBOLS)
    parser.add_argument("--feed", choices=FEEDS, default=None,
                        help="only this feed; default is both")
    parser.add_argument("--retry", type=int, default=3,
                        help="attempts per symbol per feed")
    parser.add_argument("--force", action="store_true",
                        help="refetch even where a completion stamp exists")
    args = parser.parse_args()

    os.makedirs(DATA, exist_ok=True)
    feeds = [args.feed] if args.feed else FEEDS
    jobs = [(s, f) for f in feeds for s in args.symbols]

    note("=" * 68)
    note("starting: %d jobs, %s to %s (both ends inclusive)" % (len(jobs), START, END))

    done, skipped, failed = [], [], []
    for symbol, feed in jobs:
        if already_have(symbol, feed) and not args.force:
            note("%s %s: already complete, skipping" % (symbol, feed))
            skipped.append((symbol, feed))
            continue
        (done if fetch(symbol, feed, args.retry) else failed).append((symbol, feed))

    note("-" * 68)
    note("finished: %d downloaded, %d skipped, %d failed"
         % (len(done), len(skipped), len(failed)))
    if failed:
        note("failed: " + ", ".join("%s/%s" % pair for pair in failed))
        note("rerun this script -- completed downloads are skipped")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
