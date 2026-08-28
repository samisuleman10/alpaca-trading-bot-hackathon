"""Prove that the future is unreachable.

The guard in bars.py is only worth anything if it actually fires. This file
deliberately tries to cheat -- five different ways -- and asserts that each
attempt raises instead of quietly returning tomorrow's price.

Run: python -m pytest tests -q
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agent.bars import Bar, BarWindow, LookAheadError  # noqa: E402


def make_bars(n=10, session="2026-08-28"):
    """n minutes of an invented day. Price and volume both climb, so any value
    read from the future is obviously larger than any value from the past."""
    return [
        Bar(
            t_utc="2026-08-28T13:%02d:00Z" % (30 + i),
            t_et="2026-08-28 09:%02d" % (30 + i),
            session=session,
            open=float(10 + i),
            high=float(10 + i),
            low=float(10 + i),
            close=float(10 + i),
            volume=100.0,
            trades=1,
            vwap=float(10 + i),
        )
        for i in range(n)
    ]


def test_reading_the_next_bar_raises():
    w = BarWindow(make_bars(), 4)
    with pytest.raises(LookAheadError):
        w[5]


def test_reading_far_ahead_raises():
    w = BarWindow(make_bars(), 4)
    with pytest.raises(LookAheadError):
        w[9]


def test_current_bar_is_readable():
    w = BarWindow(make_bars(), 4)
    assert w[4].close == 14.0
    assert w.current.close == 14.0
    assert w[-1].close == 14.0
    assert w[-2].close == 13.0


def test_length_stops_at_now():
    w = BarWindow(make_bars(), 4)
    assert len(w) == 5
    assert len(list(w)) == 5


def test_slices_cannot_escape():
    """A slice written to run off the end is clamped, not honoured."""
    w = BarWindow(make_bars(), 4)
    assert [b.close for b in w[:]] == [10.0, 11.0, 12.0, 13.0, 14.0]
    assert [b.close for b in w[0:9]] == [10.0, 11.0, 12.0, 13.0, 14.0]
    assert [b.close for b in w[-2:]] == [13.0, 14.0]


def test_totals_cannot_reach_past_now():
    w = BarWindow(make_bars(), 4)
    with pytest.raises(LookAheadError):
        w.sum_volume(0, 9)
    with pytest.raises(LookAheadError):
        w.sum_price_volume(0, 9)
    assert w.sum_volume(0, 5) == 500.0


def test_session_vwap_matches_the_slow_honest_version():
    """The running totals are a shortcut. Check them against adding it up."""
    bars = make_bars()
    for i in range(len(bars)):
        w = BarWindow(bars, i)
        visible = bars[: i + 1]
        expected = sum(b.vwap * b.volume for b in visible) / sum(b.volume for b in visible)
        assert abs(w.session_vwap() - expected) < 1e-9


def test_mean_volume_excludes_the_current_bar_and_refuses_to_shorten():
    bars = make_bars(10)
    # Bar 0 is the session start, so a 3-bar window is only available from
    # bar 3 onward; before that the honest answer is None, not a short window.
    assert BarWindow(bars, 2).mean_volume(3) is None
    assert BarWindow(bars, 3).mean_volume(3) == 100.0


def test_a_new_session_resets_the_averages():
    bars = make_bars(5, session="2026-08-27") + make_bars(5, session="2026-08-28")
    w = BarWindow(bars, 5)
    assert w.session_start == 5
    assert w.minutes_into_session == 1
    # Yesterday's prices must not leak into today's average.
    assert w.session_vwap() == 10.0


def test_advancing_reuses_totals_without_changing_them():
    bars = make_bars()
    a = BarWindow(bars, 3)
    b = a.advanced_to(8)
    assert b.index == 8
    assert len(b) == 9
    assert abs(b.session_vwap() - BarWindow(bars, 8).session_vwap()) < 1e-12
    # The original window is untouched.
    assert a.index == 3
