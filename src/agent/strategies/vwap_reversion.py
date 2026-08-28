"""The trading rule, and nothing else.

**What it believes.** Over a single day, a share's price wanders around the
average price everyone has paid so far -- its VWAP. When it drops noticeably
below that average on a burst of unusual trading activity, it tends to come
back. Buy the drop, sell the recovery.

**Where it came from.** The competition organisers handed this rule to every
entrant as a starting point, with no evidence that it works. Not one of its
numbers has any stated provenance. That is why every one of them lives in
params.py and gets swept: they are somebody else's settings until we have
measured them ourselves.

**The arithmetic it has to beat, written down before any result exists.**
Entering half a per cent below the average and exiting a tenth of a per cent
below it earns about 0.40%. The stop cuts at 0.50%. Risking 0.5 to make 0.4
means being right 55.6% of the time merely to break even -- before costs. That
number was recorded in advance so it cannot be moved later.

**What this file may do.** Read the price history it is handed, and return an
opinion. That is all. It does not touch the network, the clock, the account
balance, or any file, and it imports nothing outside Python's standard library.
A test enforces that by reading the file, so the rule cannot quietly acquire a
dependency on the outside world. It does not know what an option is, it does
not know how large a position is, and it cannot close one.

It is allowed to be wrong. It must not be able to be ruinous.
"""

from __future__ import annotations

from typing import Optional

from ..bars import BarWindow
from ..contracts import UP, Position, View
from ..params import StrategyParams


def decide(
    bars: BarWindow,
    position: Optional[Position],
    params: StrategyParams,
) -> Optional[View]:
    """Look at the minute that just closed and either form an opinion or not.

    Returns a View when the rule fires, and None the rest of the time -- which
    is the overwhelming majority of minutes. Those None minutes are recorded
    too: the journal keeps a row for every minute the rule was consulted, not
    only the ones it acted on, so the record can show "we looked 1,900 times
    and acted 11 times" rather than only the eleven.
    """
    # Never stack a second position on the first. The driver already only asks
    # when we are flat; this makes the rule correct on its own terms rather
    # than correct because of where it happens to be called from.
    if position is not None:
        return None

    bar = bars.current

    # No new entries late in the day. A position opened at 15:44 gets one
    # minute to work before the flat-by rule closes it, which is not the
    # strategy being tested -- it is a coin flip wearing its clothes.
    if bar.t_et[11:16] >= params.no_new_entry_after:
        return None

    average_paid = bars.session_vwap()
    usual_volume = bars.mean_volume(params.volume_window)
    if average_paid is None or average_paid <= 0 or usual_volume is None or usual_volume <= 0:
        # Not enough of the day has happened yet to have an opinion. Early in
        # the session this is the normal answer, not an error.
        return None

    # How far below the day's average price we are, as a fraction. Negative
    # means cheap.
    distance = (bar.close - average_paid) / average_paid

    # How busy this minute was against the twenty before it. 1.0 is ordinary,
    # 2.0 is twice the usual. The twenty earlier bars deliberately exclude this
    # one -- see bars.mean_volume for why.
    volume_ratio = bar.volume / usual_volume

    if distance > params.entry_distance:
        return None
    if volume_ratio < params.volume_ratio_min:
        return None

    return View(
        direction=UP,
        # Conviction is fixed at 1.0, and that is a decision rather than a
        # placeholder. The expression layer scales position size by it, so any
        # curve put here would be another unmeasured parameter deciding how
        # much money goes on each trade. Until we have evidence that a bigger
        # drop predicts a bigger bounce, every signal is sized the same and the
        # size-scaling path is inert. It is named here rather than left to be
        # discovered, because a constraint that looks active while doing
        # nothing is how a risk layer comes to be trusted for no reason.
        conviction=1.0,
        reason=(
            "%s is %.2f%% below the average price paid so far today, on %.1f times "
            "the usual minute's trading -- betting it comes back."
            % (bar.session, distance * 100.0, volume_ratio)
        ),
        evidence={
            "close": bar.close,
            "session_vwap": average_paid,
            "distance": distance,
            "volume": bar.volume,
            "mean_volume": usual_volume,
            "volume_ratio": volume_ratio,
            "minutes_into_session": float(bars.minutes_into_session),
        },
    )


def exit_levels(entry_price: float, params: StrategyParams):
    """Where the stop and the target sit, given the price we actually got.

    Both are written on the **share** price, never on the option's price. An
    option's price jumps around for reasons that have nothing to do with the
    shares -- so a stop placed on the option is a more or less random exit.
    The trigger is "get out if SPY crosses this"; when it fires, the contract
    is sold at whatever it happens to be worth.

    The stop is measured from the price we were filled at, not the price that
    triggered the signal. Those differ, and using the wrong one flatters the
    result.

    Returns (stop, target). The target is not optional: a stop without a target
    is not a strategy, it is a slow bleed.
    """
    stop = entry_price * (1.0 - params.stop_loss)
    # The target is "back to roughly the day's average price". The driver knows
    # today's average and passes it in via `target_from_vwap` below; this
    # simpler form exists for the fixed-distance ablation variant.
    return stop, entry_price


def target_from_vwap(session_vwap: float, params: StrategyParams) -> float:
    """The price at which the bounce counts as having happened.

    `exit_distance` of -0.001 means "one tenth of a per cent below the day's
    average is close enough". The average moves during the day, so this is
    recomputed each minute rather than fixed at entry.
    """
    return session_vwap * (1.0 + params.exit_distance)
