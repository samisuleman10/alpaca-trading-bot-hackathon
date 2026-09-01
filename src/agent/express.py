"""Turning an opinion about shares into a specific contract to buy.

**Why this is a separate file.** The strategy says "SPY looks cheap". That is
one claim. Choosing *which* option contract expresses it -- which expiry, which
strike, how many, at what price, and whether the trade is worth taking at all
-- is a completely different set of claims, and they can fail independently. A
good opinion expressed through a terrible contract loses money, and if the two
live in one function the post-mortem cannot tell you which half was wrong.

**What an option is, in one paragraph.** A call option is the right to buy 100
shares at a fixed price -- the *strike* -- up until a fixed date, the *expiry*.
It costs a *premium*, quoted per share, so a contract quoted at $1.63 costs
$163. If the shares end up above the strike the contract is worth something; if
they do not, it expires worthless and the whole $163 is gone. That is the trade
we are making: a small amount of money that can go to zero, controlling a much
larger amount of stock. Everything here sizes on the premium -- the money that
can actually be lost -- and never on the roughly $77,000 of shares one SPY
contract controls.

**The spread, and why it gets a hard gate.** Two prices are quoted at all
times: the *bid*, what someone will pay us, and the *ask*, what we must pay.
We buy at the ask and sell at the bid, so the gap between them is a cost, and
it is paid twice on every round trip. On 28 August we watched one contract's
gap go from one cent to five cents and back inside twenty-six seconds. Five
cents on a $1.63 contract is 3% of the money at risk, paid twice -- against a
strategy whose entire hoped-for edge is 0.4%. So a spread over the threshold is
not a worse trade, it is a trade that cannot win, and this file **declines** it
and writes down why.

**What this file may not do.** It does not know the account balance, the number
of open positions, or the daily loss so far. It proposes; risk.py disposes. The
budget it sizes against is handed to it. That separation is what lets the risk
layer be ten lines somebody can check by eye.
"""

from __future__ import annotations

import math
from typing import List, Optional, Sequence, Union

from .broker import Quote
from .contracts import UP, Decline, Intent, View
from .params import ExpressionParams

# One option contract covers this many shares. It is the number that turns a
# quoted premium into actual dollars, and getting it wrong scales every
# position by 100 in one direction or the other.
SHARES_PER_CONTRACT = 100


def express(
    view: View,
    quotes: Sequence[Quote],
    underlying: str,
    underlying_price: float,
    stop_underlying: float,
    target_underlying: float,
    budget: float,
    params: ExpressionParams,
) -> Union[Intent, Decline]:
    """Name the contract that expresses this view, or say why we won't.

    Returns an Intent to be checked by the risk layer, or a Decline carrying
    the reason. It never returns None: a refusal is a recorded fact with its
    own row in the journal, because "the spread was too wide 40 times this
    week" is a finding and a silent gap is not.
    """
    right = "call" if view.direction == UP else "put"

    candidates = [q for q in quotes if q.right == right]
    if not candidates:
        return Decline(
            reason="no %s contracts came back for that expiry and strike band" % right,
            evidence={"quotes_seen": float(len(quotes))},
        )

    # The strike we want. `strike_offset` of 0 means "as close to today's price
    # as the listed strikes allow" -- at the money. Pushing it further out
    # buys a cheaper contract that needs a bigger move to pay anything.
    wanted = underlying_price + params.strike_offset
    chosen = min(candidates, key=lambda q: abs(q.strike - wanted))

    evidence = {
        "underlying_price": underlying_price,
        "wanted_strike": wanted,
        "strike": chosen.strike,
        "bid": chosen.bid,
        "ask": chosen.ask,
        "mid": chosen.mid,
        "spread": chosen.spread,
        "spread_fraction": chosen.spread_fraction,
        "budget": budget,
    }

    # A contract with no offer to sell has no price at which we could buy it.
    # This is not the same as a wide spread and is recorded separately, because
    # "nobody is quoting" and "the quote is bad" are different market states.
    if chosen.ask <= 0.0 or chosen.bid <= 0.0:
        return Decline(
            reason="%s is not being quoted on both sides right now" % chosen.contract,
            evidence=evidence,
        )

    # The gate. See the spread paragraph above: over this threshold the round
    # trip costs more than the strategy's entire hoped-for gain.
    if chosen.spread_fraction > params.max_spread_fraction:
        return Decline(
            reason=(
                "the gap between the buying and selling price of %s is %.1f%% of its "
                "own value, over the %.1f%% we allow -- the round trip would cost more "
                "than the move we are betting on"
                % (chosen.contract, chosen.spread_fraction * 100.0,
                   params.max_spread_fraction * 100.0)
            ),
            evidence=evidence,
        )

    # What we are willing to pay. Starting from the midpoint rather than the
    # ask, plus a small allowance, so we are not automatically paying the full
    # spread on the way in. If it does not fill within the minute the order is
    # cancelled and the trade is simply not taken -- and that miss is counted.
    # A strategy that only works when we chase the price is a strategy we do
    # not have.
    limit_price = chosen.mid * (1.0 + params.limit_allowance_fraction)
    limit_price = min(limit_price, chosen.ask)
    limit_price = math.ceil(limit_price * 100.0) / 100.0   # options price in cents

    cost_of_one = limit_price * SHARES_PER_CONTRACT
    # Conviction scales the size. Every current strategy returns 1.0, which is
    # recorded in vwap_reversion.py as a deliberate choice rather than a
    # placeholder -- so today this multiplies by one and the path is inert.
    allowed = budget * view.conviction
    quantity = int(allowed // cost_of_one)

    evidence["limit_price"] = limit_price
    evidence["cost_of_one_contract"] = cost_of_one

    if quantity < 1:
        return Decline(
            reason=(
                "one contract of %s costs $%.0f, more than the $%.0f this trade is "
                "allowed to risk" % (chosen.contract, cost_of_one, allowed)
            ),
            evidence=evidence,
        )

    return Intent(
        contract=chosen.contract,
        underlying=underlying,
        right=right,
        strike=chosen.strike,
        expiry=chosen.expiry,
        quantity=quantity,
        limit_price=limit_price,
        premium_at_risk=quantity * cost_of_one,
        stop_underlying=stop_underlying,
        target_underlying=target_underlying,
        reason=(
            "%s Buying %d %s at $%.2f -- $%.0f at risk, all of which can go to zero."
            % (view.reason, quantity, chosen.contract, limit_price, quantity * cost_of_one)
        ),
        evidence=dict(view.evidence, **evidence),
    )
