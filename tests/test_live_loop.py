"""Starting the trader at the wrong moment.

The loop itself needs a live market to exercise, but one piece of it can be
tested cold: what the trader does when it is launched and the market is not
open. There are two right answers and they are different. Launched ten
minutes early, it should wait -- exiting there costs a whole session and
nobody finds out until the session is over. Launched the night before, it
should exit, because a program that sleeps for seventeen hours is not a
program anybody can reason about.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from agent.live import MAX_WAIT_FOR_OPEN_SECONDS, _seconds_until_open  # noqa: E402


def clock_opening_in(delta):
    opens_at = datetime.now(timezone(timedelta(hours=-4))) + delta
    return {"is_open": False, "next_open": opens_at.isoformat()}


def test_ten_minutes_early_is_a_wait_not_an_exit():
    seconds = _seconds_until_open(clock_opening_in(timedelta(minutes=10)))
    assert seconds is not None
    assert 500 < seconds <= 600
    assert seconds <= MAX_WAIT_FOR_OPEN_SECONDS


def test_the_night_before_is_too_far_to_wait():
    """17 hours. The answer is to exit and say so, not to sleep through it."""
    seconds = _seconds_until_open(clock_opening_in(timedelta(hours=17)))
    assert seconds > MAX_WAIT_FOR_OPEN_SECONDS


def test_a_time_already_past_is_not_something_to_wait_for():
    assert _seconds_until_open(clock_opening_in(timedelta(minutes=-5))) is None


def test_an_unreadable_or_missing_clock_never_waits():
    """Guessing about market hours is how a system ends up trading a holiday.
    If we cannot read the answer we do not invent one."""
    assert _seconds_until_open({}) is None
    assert _seconds_until_open({"next_open": ""}) is None
    assert _seconds_until_open({"next_open": "sometime tomorrow"}) is None
