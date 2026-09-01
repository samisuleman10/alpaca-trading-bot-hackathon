"""Every threshold in the system, in one place, as data.

Not one number below is typed into the trading rules themselves. There are
three reasons, and all of them matter more than the small inconvenience:

1. **Sweeping.** A threshold that lives in the rules can only be tested by
   editing the rules. Every number here gets run across a range and a human
   reads the table. None of them arrived with any evidence behind it -- the
   entry distance, the volume ratio, the fifteen-minute exit and the rest came
   from the competition organisers with no stated provenance. They are somebody
   else's in-sample settings until we have measured them ourselves.

2. **The record.** Every journal row carries `params_hash`, a short fingerprint
   of the exact settings in force at that moment. Without it, "the strategy
   said buy" is not a reproducible claim.

3. **Honesty.** If the numbers can be edited between running the backtest and
   writing the report, then "we fixed these in advance" is unverifiable. Here,
   the fingerprint in the results file either matches the settings in the
   write-up or it does not.

Times are New York local, written as "HH:MM", because that is how the trading
day is defined. Everything stored or compared is UTC; the conversion happens at
the edges. See docs/design.md section 5.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from typing import Any, Dict


@dataclass(frozen=True)
class StrategyParams:
    """The trading rule's thresholds. Swept; none is believed yet."""

    # How far below the day's average price counts as "cheap enough to buy".
    # -0.005 is half a percent below.
    entry_distance: float = -0.005

    # How close back to the average counts as "recovered, take the profit".
    exit_distance: float = -0.001

    # How much busier than usual this minute has to be. 1.20 means twenty per
    # cent above the recent average. Warning carried from the design: a rule
    # about volume does not mean the same thing in the backtest as it does
    # live, because the two use different price sources. It has to survive
    # both feeds or be dropped.
    volume_ratio_min: float = 1.20

    # How many earlier minutes the "usual" volume is averaged over.
    volume_window: int = 20

    # Give up and get out after this many minutes, win or lose.
    max_hold_minutes: int = 15

    # Get out if the share price falls this far below what we paid. Measured
    # from the price we actually got filled at, not the price that triggered
    # the signal.
    stop_loss: float = 0.005

    # Do not enter the same symbol again within this many minutes. The clock
    # starts at entry, not at exit.
    cooldown_minutes: int = 10

    # No new positions after this time. Separate from the flat-by time in the
    # risk limits, and always earlier than it.
    no_new_entry_after: str = "15:40"


@dataclass(frozen=True)
class ExpressionParams:
    """How an opinion about shares becomes a specific option contract."""

    # How many calendar days until the contract expires. Swept from day one:
    # a same-day contract is cheap and dies fast, a month out is expensive and
    # barely moves.
    target_days_to_expiry: int = 1

    # How far the strike sits from the current share price, in dollars.
    # Positive means further out -- cheaper, and needs a bigger move to pay.
    strike_offset: float = 0.0

    # Refuse to trade if the gap between the buying and selling price is wider
    # than this fraction of the contract's own price. Measured this morning,
    # the same contract went from a one-cent gap to a five-cent gap and back
    # inside twenty-six seconds; on a $1.63 contract that five cents is 3% of
    # the money at risk, and it is paid twice.
    max_spread_fraction: float = 0.05

    # How much above the midpoint we are willing to pay to get filled. If the
    # order does not fill within the minute it is cancelled and the trade is
    # simply not taken. Missed entries are counted and reported: a strategy
    # that only works when we chase the price is a strategy we do not have.
    limit_allowance_fraction: float = 0.02


@dataclass(frozen=True)
class RiskLimits:
    """The hard limits. Approved by Sami on 2026-08-28.

    The account holds $100,000 of pretend money. These are the only numbers in
    this file that were not chosen to be swept -- they are chosen to be obeyed.
    The risk layer can only ever say no, or smaller.
    """

    account_equity: float = 100_000.0

    # Most we will spend on any one trade. One per cent of the account.
    max_premium_per_trade: float = 1_000.0

    # Most positions open at once, so at most $2,000 exposed.
    max_open_positions: int = 2

    # Stop trading for the day after losing this much, counting open positions
    # at their current value. Two per cent of the account. This can prevent a
    # recovery. It stays anyway.
    daily_loss_limit: float = 2_000.0

    # Everything is closed by this time, no exceptions. An independent job on a
    # separate machine sweeps five minutes later, because a same-day contract
    # left to expire either dies worthless or turns into 100 actual shares per
    # contract -- about $77,000 that was never budgeted for.
    flat_by: str = "15:45"
    flattener_at: str = "15:50"

    # Buy contracts only. Never sell one we do not own. Asserted in our own
    # code so the guarantee does not depend on a setting in someone else's
    # dashboard.
    buy_only: bool = True

    def reduced(self) -> "RiskLimits":
        """The pre-committed fallback: every money figure divided by four.

        Used when the backtest fails to produce a setting that clears costs.
        The design fixes this in advance precisely so it cannot be invented
        after seeing the result -- a fallback chosen afterwards is not a
        fallback, it is a rationalisation.
        """
        return replace(
            self,
            max_premium_per_trade=self.max_premium_per_trade / 4.0,
            max_open_positions=1,
            daily_loss_limit=self.daily_loss_limit / 4.0,
        )


@dataclass(frozen=True)
class Config:
    """Everything the system needs to know, other than prices.

    `strategy` names a rule in the registry. Switching strategies is a change
    to this line, never a change to the trader, the risk layer, the journal or
    the dashboard.
    """

    strategy: str = "vwap_reversion"
    underlying: str = "SPY"

    # SIP is the consolidated feed carrying every US exchange, and it is what
    # every backtest in this repository ran on. But on this subscription SIP is
    # delayed by fifteen minutes, so a live trader reading it decides at 15:48
    # using the market as it looked at 15:33. IEX is a single exchange carrying
    # roughly 4% of the volume and it answers in real time, to the second, so
    # that is what the live trader reads. The backtest scripts pass --feed
    # explicitly and are unaffected by this default.
    feed: str = "iex"

    # The account this system is allowed to touch, checked against the broker
    # before the first order of every session. Not a convenience: an
    # ALPACA_API_KEY sitting in the environment silently overrides the CLI
    # profile on every command, so the tool can report the right profile while
    # talking to the wrong account. An empty string turns the check off.
    expected_account: str = "PA3JTED9VTZY"
    version: str = "0.1.0"
    strategy_params: StrategyParams = StrategyParams()
    expression: ExpressionParams = ExpressionParams()
    risk: RiskLimits = RiskLimits()

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def params_hash(self) -> str:
        """A short fingerprint of these exact settings.

        Sorted keys and a fixed separator, so the same settings always produce
        the same fingerprint on any machine, in any Python. Sixteen characters
        is plenty to tell two configurations apart and short enough to read in
        a table.
        """
        canonical = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
