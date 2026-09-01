"""The limits. This layer can say no, or smaller. It can never say more.

Read this file before any other. It is deliberately short enough to check by
eye in under a minute, because a safety rule nobody can read is a safety rule
nobody is checking.

**Why it is separate from everything else.** The strategy is allowed to be
wrong -- that is what a strategy is. It must not be allowed to be ruinous. So
the question "is this a good trade?" and the question "how much of this account
may that trade put at risk?" are answered in different files by different code,
and the second one runs last and has the final word.

**Why there is no path that increases anything.** Every branch below either
returns a refusal, or returns the intent with its quantity reduced, or passes
it through unchanged. There is deliberately no outcome meaning "yes, and
bigger". A property test in tests/test_risk_layer.py throws random intents at
this and asserts that no input ever produces a larger position than it started
with -- so the guarantee is checked mechanically, not just asserted here.

**The limits themselves**, approved by name on 2026-08-28, before any result
existed to rationalise them:

- **$1,000 of premium per trade.** One per cent of the account. Premium is the
  money that can actually go to zero, which is what we size on -- never the
  ~$77,000 of shares a single SPY contract controls.
- **Two positions open at once**, so at most $2,000 exposed.
- **Stop for the day after losing $2,000**, counting open positions at what
  they are worth right now, not at what we hope they become. Two per cent. This
  rule can and will sometimes prevent a recovery. It stays anyway: a limit that
  is lifted when it becomes inconvenient was never a limit.
- **Flat by 15:45 New York.** A separate job on a separate machine sweeps at
  15:50 regardless of whether this program is alive -- see flattener.py.
- **Buy contracts only.** We never sell an option we do not own. Selling one
  we do not own has, in the worst case, no upper bound on the loss. Asserted
  here in our own code so the guarantee does not depend on a setting in
  somebody else's dashboard.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Optional

from .contracts import ALLOWED, REFUSED, SHRUNK, Intent, Verdict
from .express import SHARES_PER_CONTRACT
from .params import RiskLimits


def check(
    intent: Intent,
    limits: RiskLimits,
    open_positions: int,
    profit_and_loss_today: float,
    now_et: str,
) -> Verdict:
    """The last word before an order can be placed.

    `now_et` is a New York wall-clock reading as "HH:MM". `profit_and_loss_today`
    is negative when we are down, and includes open positions at their current
    value -- a loss we are still holding is still a loss.
    """
    # Buy-only. Structurally the broker offers no way to open a short, so this
    # is belt and braces -- which is the correct amount of braces for the one
    # rule whose worst case has no upper bound.
    if not limits.buy_only:
        return Verdict(REFUSED, "buy-only has been switched off; refusing to trade at all")
    if intent.quantity <= 0:
        return Verdict(REFUSED, "an order for %d contracts is not an order" % intent.quantity)

    # Nothing new once the day is closing down. Separate from, and always
    # earlier than, the flat-by time itself.
    if now_et >= limits.flat_by:
        return Verdict(
            REFUSED,
            "it is %s and everything is closed by %s; no new positions" % (now_et, limits.flat_by),
        )

    # The daily stop. Checked before size, because when this bites the answer
    # is no at any size.
    if profit_and_loss_today <= -limits.daily_loss_limit:
        return Verdict(
            REFUSED,
            "down $%.0f today against a $%.0f limit; stopped for the session"
            % (-profit_and_loss_today, limits.daily_loss_limit),
        )

    if open_positions >= limits.max_open_positions:
        return Verdict(
            REFUSED,
            "already holding %d positions, the most allowed" % open_positions,
        )

    # Size. The only branch that changes the order rather than rejecting it,
    # and it only ever divides.
    cost_of_one = intent.limit_price * SHARES_PER_CONTRACT
    affordable = int(limits.max_premium_per_trade // cost_of_one)
    if affordable < 1:
        return Verdict(
            REFUSED,
            "one contract costs $%.0f, over the $%.0f allowed on a single trade"
            % (cost_of_one, limits.max_premium_per_trade),
        )

    if intent.quantity > affordable:
        smaller = replace(
            intent,
            quantity=affordable,
            premium_at_risk=affordable * cost_of_one,
        )
        return Verdict(
            SHRUNK,
            "cut from %d contracts to %d to stay under the $%.0f per-trade limit"
            % (intent.quantity, affordable, limits.max_premium_per_trade),
            smaller,
        )

    return Verdict(
        ALLOWED,
        "$%.0f at risk, within every limit" % intent.premium_at_risk,
        intent,
    )
