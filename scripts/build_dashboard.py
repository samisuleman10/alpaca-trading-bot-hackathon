"""Turn the journal into the page.

The agent writes three streams of JSON Lines as it runs -- what it thought
every minute, what it sent to the broker, and how each day began and ended.
This reads them and produces one file, `dashboard/data.js`, which the page
loads directly. No database, no server, no build step.

**Why no database.** The record is the JSONL files. A database sitting between
them and the page is one more thing that can be out of date, one more thing
that can be down during a session, and one more place where the numbers on the
screen could stop matching the numbers on disk. The page reads the record.

**The funnel is the point of this page.** A trading dashboard that shows only
trades hides the interesting part. This agent looks at the market roughly 390
times a day and does nothing on almost all of them, and each of those is a
decision with a reason attached. So the headline is not the profit, it is:

    looked 390 times
      -> formed an opinion on 34
        -> could name a contract at an acceptable price on 19
          -> was allowed to buy on 11
            -> actually filled 11

Every step of that narrowing is a rule doing its job, and every drop between
two steps is countable and explainable from the rows.

Usage:
    python scripts/build_dashboard.py                    # from journal/
    python scripts/build_dashboard.py --journal journal_example
    python scripts/build_dashboard.py --make-example     # invent a session first
"""

from __future__ import annotations

import argparse
import glob
import io
import json
import os
import random
from collections import Counter, OrderedDict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# What each decision means, in words a reader can use without reading the code.
# The order here is the order the funnel narrows in.
ACTION_MEANING = OrderedDict([
    ("no_signal", "Looked, and the rule saw nothing worth acting on."),
    ("skipped_no_bar", "No price arrived in time, so no decision was made."),
    ("cooldown", "Just closed a trade here; waiting before considering another."),
    ("holding", "Already in a position, so watching it rather than looking for a new one."),
    ("no_chain", "Formed a view, but the broker returned no contracts to choose from."),
    ("declined", "Formed a view, but no contract could be bought at an acceptable price."),
    ("refused", "Wanted to trade, and a risk rule said no."),
    ("shrunk", "Wanted to trade bigger than the limits allow, so the size was cut."),
    ("entered", "Bought a contract."),
    ("missed", "Sent an order and it did not fill in time, so it was cancelled."),
    ("entry_failed", "Tried to buy and the broker rejected it."),
    ("exit_target", "Sold: the price reached the profit target."),
    ("exit_stop", "Sold: the price reached the stop loss."),
    ("exit_time", "Sold: the position had been held long enough."),
    ("exit_flat_by", "Sold: the end-of-day cutoff arrived and everything is closed."),
    ("exit_failed", "Tried to sell and the broker rejected it."),
    ("error", "Something went wrong this minute and it was skipped."),
])

# Which actions mean the agent had actually formed an opinion about direction.
FORMED_A_VIEW = {"no_chain", "declined", "refused", "shrunk", "entered"}
# Which mean it got as far as naming a specific contract at an acceptable price.
NAMED_A_CONTRACT = {"refused", "shrunk", "entered"}
EXITS = {"exit_target", "exit_stop", "exit_time", "exit_flat_by"}


def read_stream(root, session, stream):
    path = os.path.join(root, "%s_%s.jsonl" % (session, stream))
    rows = []
    if not os.path.exists(path):
        return rows
    with io.open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                # A process killed mid-write costs at most the last line. Say so
                # rather than dropping it silently.
                rows.append({"action": "error", "reason": "unreadable journal line"})
    return rows


def sessions_in(root):
    found = set()
    for path in glob.glob(os.path.join(root, "*_decisions.jsonl")):
        found.add(os.path.basename(path).split("_decisions.jsonl")[0])
    for path in glob.glob(os.path.join(root, "*_sessions.jsonl")):
        found.add(os.path.basename(path).split("_sessions.jsonl")[0])
    return sorted(found)


def summarise(root, session):
    decisions = read_stream(root, session, "decisions")
    orders = read_stream(root, session, "orders")
    events = read_stream(root, session, "sessions")

    counts = Counter(row.get("action", "?") for row in decisions)
    looked = len(decisions)
    viewed = sum(counts[a] for a in FORMED_A_VIEW)
    named = sum(counts[a] for a in NAMED_A_CONTRACT)
    allowed = counts["entered"] + counts["shrunk"] + counts["missed"] + counts["entry_failed"]
    filled = counts["entered"]

    start = next((e for e in events if e.get("event") == "start"), {})
    end = next((e for e in events if e.get("event") == "end"), {})
    opening = (start.get("detail") or {}).get("equity")
    closing = (end.get("detail") or {}).get("closing_equity")

    # Why we said no, grouped. A refusal that happened forty times is a finding;
    # forty separate rows are not.
    refusal_reasons = Counter()
    for row in decisions:
        if row.get("action") in ("declined", "refused"):
            refusal_reasons[row.get("reason", "")] += 1

    return {
        "session": session,
        "account": (start.get("detail") or {}).get("account_number"),
        "feed": (start.get("detail") or {}).get("feed"),
        "dry_run": bool((start.get("detail") or {}).get("dry_run")),
        "limits": (start.get("detail") or {}).get("limits") or {},
        "opening_equity": opening,
        "closing_equity": closing,
        "pnl": (None if opening is None or closing is None
                else round(float(closing) - float(opening), 2)),
        "funnel": [
            {"label": "Looked at the market", "n": looked,
             "note": "One decision per minute the market was open."},
            {"label": "Formed an opinion", "n": viewed,
             "note": "The rule saw a setup worth acting on."},
            {"label": "Found a contract it could buy", "n": named,
             "note": "A contract existed, quoted on both sides, spread inside the limit."},
            {"label": "Risk layer allowed it", "n": allowed,
             "note": "Within the per-trade cap, the position limit and the daily loss limit."},
            {"label": "Actually filled", "n": filled,
             "note": "The order was accepted and the contract was bought."},
        ],
        "counts": [{"action": a, "n": counts[a], "meaning": ACTION_MEANING.get(a, a)}
                   for a in ACTION_MEANING if counts[a]],
        "exits": {a: counts[a] for a in EXITS if counts[a]},
        "refusal_reasons": [{"reason": r, "n": n}
                            for r, n in refusal_reasons.most_common(12)],
        "decisions": decisions,
        "orders": orders,
        "events": events,
    }


def build(journal_root, out_path, is_example):
    payload = {
        "is_example": is_example,
        "sessions": [summarise(journal_root, s) for s in sessions_in(journal_root)],
    }
    body = json.dumps(payload, indent=1, sort_keys=True, default=str)
    header = ("// Generated by scripts/build_dashboard.py -- do not edit by hand.\n"
              "// Source of truth is the JSONL journal, not this file.\n")
    if not os.path.isdir(os.path.dirname(out_path)):
        os.makedirs(os.path.dirname(out_path))
    with io.open(out_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(header + "window.DASHBOARD = " + body + ";\n")
    return payload


# -- inventing a session, so the page can be built before there is real data ---

def make_example(root):
    """Write a plausible session so the page can be developed against something.

    Clearly flagged as invented everywhere it surfaces. A dashboard that cannot
    be told apart from one showing real trading is a way to mislead yourself.
    """
    rng = random.Random(20260902)
    os.path.isdir(root) or os.makedirs(root)
    session = "2026-08-29-EXAMPLE"
    dec, orders, events = [], [], []

    def row(minute, action, reason, close, **extra):
        hour, mins = 9 + (30 + minute) // 60, (30 + minute) % 60
        base = {
            "action": action, "reason": reason, "close": round(close, 2),
            "bar_t_et": "2026-08-29T%02d:%02d:00" % (hour, mins),
            "bar_t_utc": "2026-08-29T%02d:%02d:00Z" % (hour + 4, mins),
            "evidence": {}, "session": session, "params_hash": "example",
            "version": "0.1.0", "t_utc": "2026-08-29T%02d:%02d:02Z" % (hour + 4, mins),
        }
        base.update(extra)
        dec.append(base)

    price, held_until = 641.0, -1
    for minute in range(0, 375):
        price += rng.gauss(0, 0.09)
        gap = rng.gauss(0, 0.28)
        if minute <= held_until:
            row(minute, "holding", "Holding SPY250829C00642000; the stop is 638.10 "
                "and the target is 642.85.", price)
            continue
        if minute == held_until + 1 and held_until > 0:
            row(minute, "exit_time", "Sold after 15 minutes: the position had been "
                "held long enough and neither the stop nor the target was reached.",
                price)
            held_until = -1
            continue
        if gap < -0.55 and minute < 340:
            if rng.random() < 0.45:
                row(minute, "declined", "The gap between the buying and selling price "
                    "of SPY250829C00642000 is 6.1% of what the contract is worth, and "
                    "we pay that twice. Too expensive to trade.", price,
                    evidence={"spread_fraction": 0.061, "bid": 1.22, "ask": 1.30})
            elif rng.random() < 0.25:
                row(minute, "refused", "One contract costs $1,240, which is more than "
                    "the $250 this session is allowed to put at risk in a single trade.",
                    price, evidence={"cost_of_one": 1240.0, "max_premium_per_trade": 250.0})
            else:
                row(minute, "entered", "Price is 0.31% below the day's average and "
                    "volume is 1.4x usual, so bought 1 x SPY250829C00642000 at $2.31.",
                    price, evidence={"vwap_gap_pct": -0.31, "volume_ratio": 1.4})
                orders.append({
                    "kind": "buy_to_open", "client_order_id":
                        "SPY-20260829T%04d-vwap_reversion-1" % minute,
                    "request": {"symbol": "SPY250829C00642000", "side": "buy",
                                "type": "limit", "qty": "1", "limit_price": "2.31"},
                    "response": {"status": "filled", "filled_avg_price": "2.31"},
                    "error": None, "session": session,
                })
                held_until = minute + 15
        else:
            row(minute, "no_signal", "Price is close to the day's average, so there is "
                "nothing to lean against.", price,
                evidence={"vwap_gap_pct": round(gap * 0.1, 3)})

    events.append({"event": "start", "session": session, "detail": {
        "equity": 100000.0, "account_number": "EXAMPLE-NOT-A-REAL-ACCOUNT",
        "feed": "iex", "dry_run": True, "status": "ACTIVE",
        "limits": {"max_premium_per_trade": 250.0, "max_open_positions": 1,
                   "daily_loss_limit": 500.0, "flat_by": "15:45"}}})
    events.append({"event": "end", "session": session, "detail": {
        "minutes_processed": len(dec), "closing_equity": 99943.0,
        "opening_equity": 100000.0, "still_holding": None}})
    events.append({"event": "flattened", "session": session,
                   "detail": {"attempt": 1, "outcome": "account is flat"}})

    for stream, rows in (("decisions", dec), ("orders", orders), ("sessions", events)):
        path = os.path.join(root, "%s_%s.jsonl" % (session, stream))
        with io.open(path, "w", encoding="utf-8", newline="\n") as handle:
            for r in rows:
                handle.write(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n")
    return session


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--journal", default="journal")
    parser.add_argument("--out", default=os.path.join("dashboard", "data.js"))
    parser.add_argument("--make-example", action="store_true",
                        help="invent one session first, for developing the page")
    args = parser.parse_args(argv)

    root = os.path.join(ROOT, args.journal)
    if args.make_example:
        root = os.path.join(ROOT, "journal_example")
        session = make_example(root)
        print("invented %s in %s" % (session, root))

    payload = build(root, os.path.join(ROOT, args.out), is_example=args.make_example)
    for s in payload["sessions"]:
        print("%s  %s minutes  %s trades  pnl %s" % (
            s["session"], s["funnel"][0]["n"], s["funnel"][4]["n"], s["pnl"]))
    print("wrote %s" % os.path.join(ROOT, args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
