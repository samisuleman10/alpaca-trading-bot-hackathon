"""Does the rule fire when it should, and stay quiet when it should not?

These are not tests of whether the strategy makes money -- that question is
settled by the backtest, on real prices, and the answer may well be no. These
check something narrower and more important: that the code does what the
written rule says it does. A strategy that loses money for the reason it was
designed to is a result. One that loses money because of a sign error is
nothing at all.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agent.bars import Bar, BarWindow  # noqa: E402
from agent.contracts import UP, Position  # noqa: E402
from agent.params import StrategyParams  # noqa: E402
from agent.strategies import vwap_reversion  # noqa: E402

PARAMS = StrategyParams()


def bar(minute, close, volume, session="2026-08-28"):
    """One minute, at `minute` minutes past 09:30 New York."""
    hour, mins = divmod(9 * 60 + 30 + minute, 60)
    return Bar(
        t_utc="2026-08-28T%02d:%02d:00Z" % (hour + 4, mins),
        t_et="2026-08-28 %02d:%02d" % (hour, mins),
        session=session,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=float(volume),
        trades=1,
        vwap=close,
    )


def flat_day(n=30, price=100.0, volume=1000):
    """A quiet session: same price, same volume, every minute."""
    return [bar(i, price, volume) for i in range(n)]


def test_a_quiet_day_produces_no_opinion():
    bars = flat_day()
    for i in range(len(bars)):
        assert vwap_reversion.decide(BarWindow(bars, i), None, PARAMS) is None


def test_a_deep_drop_on_heavy_volume_fires():
    bars = flat_day()
    # 1% below a day that has averaged 100, on triple the usual volume.
    bars.append(bar(30, 99.0, 3000))
    view = vwap_reversion.decide(BarWindow(bars, 30), None, PARAMS)
    assert view is not None
    assert view.direction == UP
    assert view.conviction == 1.0
    assert view.evidence["distance"] < PARAMS.entry_distance
    assert view.evidence["volume_ratio"] > PARAMS.volume_ratio_min
    assert "below the average price" in view.reason


def test_a_deep_drop_on_ordinary_volume_does_not_fire():
    bars = flat_day()
    bars.append(bar(30, 99.0, 1000))
    assert vwap_reversion.decide(BarWindow(bars, 30), None, PARAMS) is None


def test_a_shallow_drop_on_heavy_volume_does_not_fire():
    bars = flat_day()
    # Only 0.1% below -- cheaper than average, but not by enough.
    bars.append(bar(30, 99.9, 3000))
    assert vwap_reversion.decide(BarWindow(bars, 30), None, PARAMS) is None


def test_a_rise_never_fires():
    """The rule only ever buys dips. A rally is not a signal, in either
    direction -- selling the strength is a different strategy that has not
    been tested."""
    bars = flat_day()
    bars.append(bar(30, 101.0, 3000))
    assert vwap_reversion.decide(BarWindow(bars, 30), None, PARAMS) is None


def test_nothing_fires_while_a_position_is_open():
    bars = flat_day()
    bars.append(bar(30, 99.0, 3000))
    held = Position(
        contract="SPY260904C00770000",
        quantity=1,
        entry_premium=1.63,
        entry_t_utc="2026-08-28T14:00:00Z",
        underlying_at_entry=100.0,
    )
    assert vwap_reversion.decide(BarWindow(bars, 30), held, PARAMS) is None


def test_nothing_fires_after_the_cutoff():
    """15:40 New York. A trade opened at 15:44 gets one minute before the
    flat-by rule closes it, which tests nothing."""
    bars = flat_day(n=370)  # 09:30 through 15:39
    bars.append(bar(370, 99.0, 3000))  # 15:40 exactly -- already too late
    assert bars[-1].t_et.endswith("15:40")
    assert vwap_reversion.decide(BarWindow(bars, 370), None, PARAMS) is None
    # One minute earlier, the same signal is taken.
    earlier = flat_day(n=369) + [bar(369, 99.0, 3000)]
    assert earlier[-1].t_et.endswith("15:39")
    assert vwap_reversion.decide(BarWindow(earlier, 369), None, PARAMS) is not None


def test_no_opinion_before_the_volume_window_has_filled():
    """Early in the session there is not yet a 'usual' volume to compare
    against, and the honest answer is silence rather than a guess."""
    bars = flat_day(n=5)
    bars.append(bar(5, 99.0, 3000))
    assert vwap_reversion.decide(BarWindow(bars, 5), None, PARAMS) is None


def test_the_stop_sits_below_the_price_we_actually_paid():
    stop, _ = vwap_reversion.exit_levels(100.0, PARAMS)
    assert abs(stop - 99.5) < 1e-9


def test_the_target_sits_just_below_the_days_average():
    assert abs(vwap_reversion.target_from_vwap(100.0, PARAMS) - 99.9) < 1e-9
