"""The conventions that decide whether the backtest is honest.

Every test here is a rule that, if broken, makes the result better than
reality rather than worse. That is the direction mistakes travel in a
backtest, and it is why these are asserted on hand-built prices where the
right answer is known by construction rather than checked against a run.
"""

from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from agent import backtest  # noqa: E402
from agent.bars import Bar  # noqa: E402
from agent.params import Config  # noqa: E402

DAY = "2024-03-01"
NEXT = "2024-03-04"


def bar(session, minute_of_day, o, h, l, c, volume=100.0):
    """One minute, addressed by New York wall-clock minutes past midnight."""
    hh, mm = divmod(minute_of_day, 60)
    t_et = "%sT%02d:%02d:00" % (session, hh, mm)
    # The UTC stamp only has to be unique and ordered for these tests; the
    # driver reads the New York clock for every decision it makes.
    t_utc = "%sT%02d:%02d:00Z" % (session, hh + 5, mm)
    return Bar(t_utc=t_utc, t_et=t_et, session=session, open=o, high=h,
               low=l, close=c, volume=volume, trades=1, vwap=0.0)


def quiet_session(session, start=570, count=25, price=100.0, volume=100.0):
    """Enough flat minutes to establish a day's average and a usual volume.

    The rule needs both before it can have an opinion, so every scenario below
    starts with a stretch of nothing happening.
    """
    return [bar(session, start + i, price, price, price, price, volume)
            for i in range(count)]


def trigger(session, minute_of_day, price=99.4, volume=300.0):
    """A minute cheap enough and busy enough to make the rule fire."""
    return bar(session, minute_of_day, price, price, price, price, volume)


def tail(session, first_minute, count, price=99.5):
    """Filler so a session does not end the instant the interesting part does."""
    return [bar(session, first_minute + i, price, price, price, price)
            for i in range(count)]


def run(bars, **kwargs):
    return backtest.run(bars, Config(), record_bars=True, **kwargs)


# ---------------------------------------------------------------------------


def test_fill_is_the_next_bars_open_not_the_signal_bars_close():
    """The rule decides on a price that has gone; it buys at the next one.

    Entering at the closing price of the bar that triggered the signal means
    buying at a price that no longer existed when the decision was made. It is
    the most flattering error a backtest can contain.
    """
    bars = quiet_session(DAY)
    bars.append(trigger(DAY, 595))                     # signal, closes at 99.4
    bars.append(bar(DAY, 596, 99.0, 99.2, 98.9, 99.1))  # opens lower
    bars += tail(DAY, 597, 30)

    result = run(bars)
    assert len(result.trades) == 1
    assert result.trades[0].entry_price == pytest.approx(99.0)
    assert result.trades[0].signal_close == pytest.approx(99.4)


def test_stop_and_target_in_one_minute_resolves_to_the_stop_and_is_counted():
    """A bar cannot say which came first, so we assume the loss -- and say so.

    The count matters as much as the choice. Without it a reader cannot tell
    whether the assumption touched two trades or two hundred.
    """
    bars = quiet_session(DAY)
    bars.append(trigger(DAY, 595))
    bars.append(bar(DAY, 596, 99.4, 99.4, 99.4, 99.4))   # fill at 99.40
    # Stop sits at 99.4 * 0.995 = 98.903; the target is just under the day's
    # average, near 99.95. This minute reaches both.
    bars.append(bar(DAY, 597, 99.4, 100.2, 98.8, 99.5))
    bars += tail(DAY, 598, 30)

    result = run(bars)
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exit_reason == backtest.EXIT_STOP
    assert trade.ambiguous_exit is True
    assert result.summary["ambiguous_exit_bars"] == 1
    assert trade.net_return < 0


def test_a_gap_through_the_stop_fills_at_the_open_not_at_the_stop():
    """If the minute opened below the stop, the stop price was never available."""
    bars = quiet_session(DAY)
    bars.append(trigger(DAY, 595))
    bars.append(bar(DAY, 596, 99.4, 99.4, 99.4, 99.4))
    bars.append(bar(DAY, 597, 98.0, 98.1, 97.9, 98.0))   # opens far below the stop
    bars += tail(DAY, 598, 30)

    result = run(bars)
    trade = result.trades[0]
    assert trade.exit_reason == backtest.EXIT_STOP
    assert trade.exit_price == pytest.approx(98.0)
    assert trade.exit_price < trade.stop


def test_a_gap_through_the_target_fills_at_the_open_which_is_better():
    """The same convention in the other direction, because it is also what happens."""
    bars = quiet_session(DAY)
    bars.append(trigger(DAY, 595))
    bars.append(bar(DAY, 596, 99.4, 99.4, 99.4, 99.4))
    bars.append(bar(DAY, 597, 100.5, 100.6, 100.4, 100.5))  # opens above the target
    bars += tail(DAY, 598, 30, price=100.5)

    result = run(bars)
    trade = result.trades[0]
    assert trade.exit_reason == backtest.EXIT_TARGET
    assert trade.exit_price == pytest.approx(100.5)
    assert trade.exit_price > trade.target_at_exit


def test_a_position_that_neither_wins_nor_loses_is_closed_on_time():
    """Fifteen minutes, then out. A held position with no thesis left is a bet."""
    bars = quiet_session(DAY)
    bars.append(trigger(DAY, 595))
    bars += [bar(DAY, 596 + i, 99.4, 99.45, 99.35, 99.4) for i in range(40)]

    result = run(bars)
    trade = result.trades[0]
    assert trade.exit_reason == backtest.EXIT_TIME
    assert trade.minutes_held == Config().strategy_params.max_hold_minutes


def test_holding_time_is_measured_in_minutes_not_in_bars():
    """A minute in which nothing trades produces no bar at all.

    Counting bars would let a position sit open far longer than the rule
    allows, in exactly the illiquid stretches where that is most dangerous.
    """
    bars = quiet_session(DAY)
    bars.append(trigger(DAY, 595))
    bars.append(bar(DAY, 596, 99.4, 99.45, 99.35, 99.4))
    # A twenty-minute hole, then one bar. Two bars have passed; twenty-one
    # minutes have.
    bars.append(bar(DAY, 617, 99.4, 99.45, 99.35, 99.4))
    bars += tail(DAY, 618, 20, price=99.4)

    result = run(bars)
    trade = result.trades[0]
    assert trade.exit_reason == backtest.EXIT_TIME
    assert trade.minutes_held == 21


def test_nothing_is_carried_overnight():
    """Two sessions, and no trade may span them.

    The driver also asserts this internally; this checks the outcome rather
    than the guard, so a future change that removes the guard still fails.
    """
    bars = quiet_session(DAY)
    bars.append(trigger(DAY, 595))
    bars += tail(DAY, 596, 5, price=99.4)          # session ends while holding
    bars += quiet_session(NEXT)
    bars += tail(NEXT, 595, 20, price=99.4)

    result = run(bars)
    assert result.trades
    for trade in result.trades:
        assert trade.session == trade.exit_t_utc[:10]
        assert trade.entry_t_utc[:10] == trade.exit_t_utc[:10]


def test_a_signal_with_no_next_minute_is_recorded_as_unfilled():
    """A trade we could not take is a fact about the strategy, not a gap.

    Silently dropping it would quietly improve the average, because the
    dropped ones are not a random sample -- they are all at the close.
    """
    bars = quiet_session(DAY)
    bars.append(trigger(DAY, 595))                 # last bar of the session
    bars += quiet_session(NEXT)

    result = run(bars)
    assert result.summary["signals_unfilled"] == 1
    assert result.trades == []
    events = [r["event"] for r in result.bar_rows]
    assert backtest.NO_FILL in events


def test_the_control_uses_the_same_machinery_and_only_changes_the_entry():
    """The coin-flip control must differ in one respect and no others."""
    bars = quiet_session(DAY)
    bars += [bar(DAY, 595 + i, 99.4, 99.5, 99.3, 99.4) for i in range(300)]

    control = backtest.run(bars, Config(), entry_mode="random",
                           entry_probability=0.5, seed=1, record_bars=False)
    assert control.trades
    assert control.summary["entry_mode"] == "random"
    # Same exits, same stop arithmetic, same power floor -- only the entry rule
    # was replaced.
    assert control.summary["break_even_win_rate"] == pytest.approx(0.5 / 0.9)
    for trade in control.trades:
        assert trade.stop == pytest.approx(trade.entry_price * 0.995)


def test_the_control_is_reproducible():
    """A different seed may differ; the same seed may not."""
    bars = quiet_session(DAY)
    bars += [bar(DAY, 595 + i, 99.4, 99.5, 99.3, 99.4) for i in range(300)]

    first = backtest.run(bars, Config(), entry_mode="random",
                         entry_probability=0.3, seed=7, record_bars=False)
    again = backtest.run(bars, Config(), entry_mode="random",
                         entry_probability=0.3, seed=7, record_bars=False)
    assert [t.entry_t_utc for t in first.trades] == [t.entry_t_utc for t in again.trades]


def test_costs_are_charged_on_both_sides():
    """Every trade pays to get in and pays again to get out."""
    bars = quiet_session(DAY)
    bars.append(trigger(DAY, 595))
    bars += [bar(DAY, 596 + i, 99.4, 99.45, 99.35, 99.4) for i in range(40)]

    free = run(bars)
    charged = run(bars, cost_fraction_per_side=0.001)
    assert charged.trades[0].gross_return == pytest.approx(free.trades[0].gross_return)
    assert charged.trades[0].net_return == pytest.approx(
        free.trades[0].net_return - 0.002)


def test_every_minute_is_recorded_not_only_the_traded_ones():
    """The per-bar file is the audit trail; a file of only trades cannot be checked."""
    bars = quiet_session(DAY)
    bars.append(trigger(DAY, 595))
    bars += tail(DAY, 596, 30)

    result = run(bars)
    assert len(result.bar_rows) == len(bars)
    assert sum(1 for r in result.bar_rows if r["event"] == "") > len(bars) / 2


def test_an_entry_with_no_room_to_its_target_is_refused_not_recorded():
    """A position that is already past its target is not a trade.

    The target is set from the day's average price, not from what we paid, so
    buying above that average opens and closes a position in the same minute
    for nothing. Nine hundred of those will drag any average toward zero and
    look like a measurement. The rule cannot reach this state -- it only buys
    below the average -- but the coin-flip control reaches it constantly, and
    a control made mostly of non-trades cannot be compared to anything.
    """
    bars = quiet_session(DAY)
    # Prices climbing away from the day's average, so every later minute sits
    # above the target that minute would be aiming at.
    bars += [bar(DAY, 595 + i, 100.0 + i * 0.05, 100.0 + i * 0.05,
                 100.0 + i * 0.05, 100.0 + i * 0.05) for i in range(100)]

    control = backtest.run(bars, Config(), entry_mode="random",
                           entry_probability=1.0, seed=3, record_bars=False)
    assert control.summary["minutes_with_no_room_to_the_target"] > 50
    assert control.trades == []


def test_the_gate_only_refuses_minutes_the_rule_refuses_anyway():
    """The gate must change the control and leave the rule untouched.

    A minute with no room to the target is a minute at or above the day's
    average price. The rule only ever buys 0.5% *below* that average, so every
    minute the gate removes is one the rule had already declined. Here the
    prices climb all session: the gate refuses nearly every minute, and the
    rule independently produces no signal at all.
    """
    bars = quiet_session(DAY)
    bars += [bar(DAY, 595 + i, 100.0 + i * 0.05, 100.0 + i * 0.05,
                 100.0 + i * 0.05, 100.0 + i * 0.05) for i in range(100)]

    result = run(bars)
    assert result.summary["minutes_with_no_room_to_the_target"] > 50
    assert result.summary["signals"] == 0
    assert result.trades == []


def test_the_gate_does_not_stand_between_the_rule_and_its_trade():
    """When the rule does fire, nothing about the gate interferes with it."""
    bars = quiet_session(DAY)
    bars.append(trigger(DAY, 595))
    bars += tail(DAY, 596, 30)

    result = run(bars)
    assert result.summary["signals"] == 1
    assert len(result.trades) == 1
    assert result.summary["signals_unfilled"] == 0
