"""Choosing the contract, and refusing when the price of trading is too high.

The gate these tests exist for: the gap between an option's buying and selling
price is a cost paid twice on every round trip, and this strategy's entire
hoped-for gain is 0.4%. A contract whose gap is 6% of its own value cannot be
traded profitably by this rule no matter how right the rule is. So the test
that matters most is that such a contract is **declined, with a reason written
down** -- not traded, and not silently dropped.
"""

from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from agent.broker import Quote  # noqa: E402
from agent.contracts import UP, Decline, Intent, View  # noqa: E402
from agent.express import SHARES_PER_CONTRACT, express  # noqa: E402
from agent.params import ExpressionParams  # noqa: E402

PRICE = 770.0


def quote(strike, bid, ask, right="call"):
    return Quote(
        contract="SPY260904%s%08d" % ("C" if right == "call" else "P", int(strike * 1000)),
        right=right, strike=strike, expiry="2026-09-04",
        bid=bid, ask=ask, bid_size=10.0, ask_size=10.0,
        t_utc="2026-09-04T14:00:00Z",
    )


def a_view():
    return View(direction=UP, conviction=1.0, reason="SPY looks cheap.",
                evidence={"close": PRICE})


def run(quotes, params=None, budget=1_000.0):
    return express(a_view(), quotes, "SPY", PRICE, 765.0, 771.0, budget,
                   params or ExpressionParams())


def test_a_wide_spread_is_declined_and_says_why():
    """Bid 1.00, ask 1.12 -- a 12-cent gap on a $1.06 midpoint is 11% of the
    contract's own value, paid twice. Against a 0.4% target that is not a
    worse trade, it is one that cannot win."""
    result = run([quote(770.0, 1.00, 1.12)])
    assert isinstance(result, Decline)
    assert "gap between the buying and selling price" in result.reason
    assert result.evidence["spread_fraction"] > 0.05


def test_a_tight_spread_is_traded():
    result = run([quote(770.0, 1.50, 1.53)])
    assert isinstance(result, Intent)
    assert result.contract == "SPY260904C00770000"
    assert result.quantity >= 1


def test_a_contract_quoted_on_only_one_side_is_declined_separately():
    """No offer to sell means no price at which we could buy. That is a
    different fact from a bad price and gets its own reason."""
    result = run([quote(770.0, 1.50, 0.0)])
    assert isinstance(result, Decline)
    assert "not being quoted on both sides" in result.reason


def test_the_strike_chosen_is_the_one_nearest_the_share_price():
    quotes = [quote(760.0, 10.0, 10.1), quote(769.0, 1.5, 1.53), quote(780.0, 0.2, 0.21)]
    result = run(quotes)
    assert isinstance(result, Intent)
    assert result.strike == 769.0


def test_size_comes_from_the_premium_not_the_shares_controlled():
    """One SPY contract controls ~$77,000 of stock and costs $150. Sizing on
    the stock would put the whole account into a single trade; sizing on the
    premium -- the money that can actually go to zero -- buys six."""
    result = run([quote(770.0, 1.50, 1.53)], budget=1_000.0)
    assert isinstance(result, Intent)
    assert result.quantity * result.limit_price * SHARES_PER_CONTRACT <= 1_000.0
    assert result.premium_at_risk < 1_000.0


def test_a_contract_costing_more_than_the_budget_is_declined():
    result = run([quote(770.0, 20.00, 20.10)], budget=1_000.0)
    assert isinstance(result, Decline)
    assert "more than the" in result.reason


def test_we_never_pay_more_than_the_asking_price():
    """The limit starts from the midpoint plus an allowance, but a generous
    allowance must never push it past the ask -- that would be paying more
    than the price being asked, which is not a thing to do."""
    generous = ExpressionParams(limit_allowance_fraction=0.50)
    result = run([quote(770.0, 1.50, 1.53)], params=generous)
    assert isinstance(result, Intent)
    assert result.limit_price <= 1.53


def test_a_view_with_no_matching_contracts_is_declined_not_crashed():
    result = run([quote(770.0, 1.5, 1.53, right="put")])
    assert isinstance(result, Decline)
    assert "no call contracts" in result.reason


def test_every_decline_carries_evidence():
    """A refusal with no numbers behind it cannot be checked later, and
    'the spread was too wide 40 times this week' is a finding only if each of
    the 40 recorded what it saw."""
    for quotes in ([quote(770.0, 1.00, 1.12)], [quote(770.0, 1.5, 0.0)],
                   [quote(770.0, 20.0, 20.1)]):
        result = run(quotes)
        assert isinstance(result, Decline)
        assert result.reason.strip()
        assert result.evidence
