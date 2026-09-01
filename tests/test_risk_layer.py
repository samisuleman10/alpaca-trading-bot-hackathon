"""The risk layer can say no, or smaller. Never more.

That sentence is the whole guarantee, and a sentence in a docstring is not a
guarantee. So the central test here throws several thousand randomly-built
orders at the layer and asserts that not one of them comes back larger than it
went in -- across every combination of limits, times of day, position counts
and losses. A future change that adds a branch quietly increasing a size fails
here without anybody having to think of that branch in advance.

The rest are the individual limits, each checked at its own boundary, because
a property test proves nothing *increases* and says nothing about whether the
right things are *refused*.
"""

from __future__ import annotations

import os
import random
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from agent.contracts import ALLOWED, REFUSED, SHRUNK, Intent  # noqa: E402
from agent.express import SHARES_PER_CONTRACT  # noqa: E402
from agent.params import RiskLimits  # noqa: E402
from agent.risk import check  # noqa: E402


def an_intent(quantity=3, limit_price=1.50):
    return Intent(
        contract="SPY260904C00770000", underlying="SPY", right="call",
        strike=770.0, expiry="2026-09-04", quantity=quantity,
        limit_price=limit_price,
        premium_at_risk=quantity * limit_price * SHARES_PER_CONTRACT,
        stop_underlying=765.0, target_underlying=771.0,
        reason="a test order",
    )


def test_no_input_ever_produces_a_bigger_position():
    """The property the whole layer exists to have.

    Five thousand random orders against random limits, random times, random
    position counts and random losses. Every single answer must be a refusal,
    or the same size, or smaller. There is no fourth outcome.
    """
    rng = random.Random(20260901)
    for _ in range(5000):
        quantity = rng.randint(1, 50)
        price = round(rng.uniform(0.05, 25.0), 2)
        intent = an_intent(quantity, price)
        limits = RiskLimits(
            max_premium_per_trade=rng.choice([50.0, 250.0, 1_000.0, 10_000.0]),
            max_open_positions=rng.randint(1, 3),
            daily_loss_limit=rng.choice([100.0, 2_000.0]),
        )
        verdict = check(
            intent, limits,
            open_positions=rng.randint(0, 3),
            profit_and_loss_today=rng.uniform(-5_000.0, 5_000.0),
            now_et="%02d:%02d" % (rng.randint(9, 15), rng.randint(0, 59)),
        )
        if verdict.outcome == REFUSED:
            assert verdict.intent is None
            continue
        assert verdict.intent is not None
        assert verdict.intent.quantity <= intent.quantity
        assert verdict.intent.premium_at_risk <= intent.premium_at_risk + 1e-9
        assert verdict.intent.premium_at_risk <= limits.max_premium_per_trade + 1e-9


def test_an_order_over_the_per_trade_limit_is_cut_not_refused():
    """Too big is a sizing problem, not a veto. Ten contracts at $1.50 is
    $1,500 of premium against a $1,000 limit, so six fit."""
    verdict = check(an_intent(10, 1.50), RiskLimits(), 0, 0.0, "10:00")
    assert verdict.outcome == SHRUNK
    assert verdict.intent.quantity == 6
    assert verdict.intent.premium_at_risk == pytest.approx(900.0)


def test_a_contract_costing_more_than_the_whole_budget_is_refused():
    """One contract at $12 costs $1,200. There is no smaller number of
    contracts than one, so the answer has to be no."""
    verdict = check(an_intent(1, 12.00), RiskLimits(), 0, 0.0, "10:00")
    assert verdict.outcome == REFUSED
    assert verdict.intent is None


def test_the_daily_loss_limit_refuses_at_any_size():
    verdict = check(an_intent(1, 1.00), RiskLimits(), 0, -2_000.0, "10:00")
    assert verdict.outcome == REFUSED
    assert "stopped for the session" in verdict.reason


def test_a_full_book_is_refused():
    verdict = check(an_intent(1, 1.00), RiskLimits(), 2, 0.0, "10:00")
    assert verdict.outcome == REFUSED


def test_nothing_new_once_the_flat_by_time_arrives():
    """At 15:45 everything is being closed. Opening something is the opposite
    of that, and the boundary itself must refuse rather than allow."""
    limits = RiskLimits()
    assert check(an_intent(), limits, 0, 0.0, "15:44").outcome == ALLOWED
    assert check(an_intent(), limits, 0, 0.0, limits.flat_by).outcome == REFUSED


def test_the_quarter_size_fallback_only_ever_reduces():
    """The pre-committed fallback used when the backtest finds nothing that
    clears costs. Every money figure must come out smaller, never larger."""
    full, quarter = RiskLimits(), RiskLimits().reduced()
    assert quarter.max_premium_per_trade < full.max_premium_per_trade
    assert quarter.daily_loss_limit < full.daily_loss_limit
    assert quarter.max_open_positions <= full.max_open_positions
    assert quarter.flat_by == full.flat_by      # timing is not a money figure
    assert quarter.buy_only is True


def test_selling_something_we_do_not_own_is_impossible_here():
    """buy_only off means the whole layer refuses, rather than permitting a
    short. Selling an option we do not own has no upper bound on the loss."""
    verdict = check(an_intent(), RiskLimits(buy_only=False), 0, 0.0, "10:00")
    assert verdict.outcome == REFUSED
