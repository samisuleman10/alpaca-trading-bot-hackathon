"""The records passed between the pieces.

These are the only shapes that cross a boundary in this system, and they are
deliberately small. Each one is frozen -- once made, it cannot be changed -- so
that a record handed to the risk layer is the same record that reaches the
journal, and nobody downstream can quietly edit history.

The chain is:

    decide()  -> View     "SPY looks cheap"           (an opinion, no contract)
    express() -> Intent   "buy 6 of SPY 26/09/04 770 call"  (an order to place)
    risk()    -> Verdict  "allowed, but 3 not 6"      (never more, only less)

Splitting the opinion from the contract is the point. It means a bad choice of
contract cannot be mistaken for a bad strategy, and each half can be tested on
its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional


# Direction is a plain string rather than an enum so it survives a round trip
# through the journal, a CSV and JSON without anybody writing a converter.
UP = "up"
DOWN = "down"

# What the risk layer can answer. There is deliberately no value here meaning
# "yes, and bigger".
ALLOWED = "allowed"
SHRUNK = "shrunk"
REFUSED = "refused"


@dataclass(frozen=True)
class Position:
    """What we hold right now. Read-only to the strategy, always.

    The strategy is told what it holds, what it paid, and when -- and cannot
    change any of it. Everything about closing a position happens in the
    driver, because after the design's section 2A nothing closes by itself.

    `underlying_at_entry` is the share price when we entered, not the option
    price. Both the stop and the profit target are written on the shares, so
    this is the number they are measured against.
    """

    contract: str
    quantity: int
    entry_premium: float
    entry_t_utc: str
    underlying_at_entry: float
    minutes_held: int = 0


@dataclass(frozen=True)
class View:
    """An opinion about where the share price is going. No contract yet.

    `conviction` runs 0 to 1 and says how strongly the rule believes its own
    answer. Exactly one thing reads it: the expression layer scales the size by
    it, so a weak signal buys less. A strategy may always return 1.0 and
    nothing breaks.

    `reason` is one sentence of plain English, and `evidence` is the raw numbers
    behind it. Both are stored on every row of the journal. They are what makes
    it possible for somebody else to recompute the rule from the record and
    confirm the system did what it claimed -- which is the whole point.
    """

    direction: str
    conviction: float
    reason: str
    evidence: Dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.direction not in (UP, DOWN):
            raise ValueError("direction must be %r or %r, got %r" % (UP, DOWN, self.direction))
        if not 0.0 <= self.conviction <= 1.0:
            raise ValueError("conviction must be between 0 and 1, got %r" % (self.conviction,))
        if not self.reason.strip():
            raise ValueError("a view must carry a reason; an unexplained decision is not auditable")


@dataclass(frozen=True)
class Intent:
    """A specific option contract to buy, and how much of it.

    One contract covers 100 shares, so `quantity` of 6 at a premium of $1.63
    costs 6 x 100 x $1.63 = $978. That number -- `premium_at_risk` -- is the
    whole cost, and all of it can go to zero. Sizing is done on it, never on
    the roughly $77,000 of shares the contract controls.
    """

    contract: str
    underlying: str
    right: str
    strike: float
    expiry: str
    quantity: int
    limit_price: float
    premium_at_risk: float
    stop_underlying: float
    target_underlying: float
    reason: str
    evidence: Dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class Decline:
    """Why the expression layer refused to name a contract.

    A decline is a recorded fact with its own journal column, not a silent
    `None`. "The spread was too wide 40 times this week" is a finding; a gap in
    the record is not.
    """

    reason: str
    evidence: Dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class Verdict:
    """The risk layer's answer. It can say no, or smaller. Never more.

    `intent` is None when the answer was `REFUSED`. When it is `SHRUNK`, the
    intent carried here is the reduced one, and `reason` says which limit bit.
    """

    outcome: str
    reason: str
    intent: Optional[Intent] = None

    def __post_init__(self) -> None:
        if self.outcome not in (ALLOWED, SHRUNK, REFUSED):
            raise ValueError("unknown risk outcome %r" % (self.outcome,))
        if self.outcome == REFUSED and self.intent is not None:
            raise ValueError("a refusal cannot carry an order")
        if self.outcome != REFUSED and self.intent is None:
            raise ValueError("an allowance must carry the order it allowed")
