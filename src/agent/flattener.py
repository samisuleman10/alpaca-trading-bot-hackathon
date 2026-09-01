"""Close everything at 15:50 New York, no matter what.

**Why this is a separate program.** It runs on its own schedule, on a different
machine, and it never asks whether the trader is healthy. That is the entire
point: the failure it guards against is the trader being dead, hung, or
confidently wrong. A safety net that depends on the thing it is catching is not
a safety net.

**What it is guarding against.** We buy same-day and next-day option contracts.
A call option left to expire while it is worth something does not quietly
vanish -- it is *exercised*, and turns into 100 actual shares per contract. At
SPY's price that is roughly $77,000 of stock per contract, bought with money
the account does not have and was never budgeted for. So: everything is closed
before the bell, every day, whether or not the trade worked and whether or not
anybody is watching.

**Why it calls twice.** Probe 3 measured this directly: `position close-all`
answered `200` with the order still `pending_new` and nothing actually sold. A
`200` from that endpoint means *accepted*, not *closed*. So this calls, waits,
asks what is actually held, and calls again -- a small fixed number of times --
then writes down what it found and what it did.

**Why it runs unattended even though Alpaca's own agent guidance says these two
commands need a human to confirm them.** That guidance is right for an agent
acting on somebody's ordinary brokerage account, where `close-all` could
liquidate a position it knows nothing about. This account trades nothing but
this system, so there is no third party's position for it to touch, and the
thing being prevented is far worse than the thing being risked.

Usage (from cron, or Task Scheduler, at 15:50 New York):
    python -m agent.flattener --journal journal
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime

from .broker import NEW_YORK, Broker, BrokerError, assert_paper_account
from .journal import Journal

ATTEMPTS = 3
WAIT_BETWEEN_SECONDS = 5


def flatten(broker: Broker, journal: Journal) -> bool:
    """Close everything. Returns True once the account is confirmed empty."""
    assert_paper_account()

    for attempt in range(1, ATTEMPTS + 1):
        held = broker.positions()
        if not held:
            journal.session_event("flattened", {
                "attempt": attempt,
                "outcome": "account is flat",
            })
            return True

        try:
            response = broker.close_all_positions()
            error = None
        except BrokerError as exc:
            response, error = None, str(exc)

        journal.session_event("flatten_attempt", {
            "attempt": attempt,
            "positions_before": held,
            "response": response,
            "error": error,
        })
        # Accepted is not closed. Give the orders a moment, then look again.
        time.sleep(WAIT_BETWEEN_SECONDS)

    still_held = broker.positions()
    journal.session_event("flatten_failed", {
        "attempts": ATTEMPTS,
        "still_held": still_held,
        "note": "The account is not flat and this program is out of attempts. "
                "This needs a person.",
    })
    return not still_held


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--journal", default="journal")
    args = parser.parse_args(argv)

    session = datetime.now(NEW_YORK).strftime("%Y-%m-%d")
    journal = Journal(args.journal, session, "flattener", "0.1.0")
    broker = Broker()

    flat = flatten(broker, journal)
    print("account is flat" if flat else "ACCOUNT IS NOT FLAT -- needs a person")
    return 0 if flat else 1


if __name__ == "__main__":
    sys.exit(main())
