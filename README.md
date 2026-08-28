# An autonomous options trading agent, and a record of everything it decided

Built for the lablab.ai × Alpaca **Options Alpha Agents** hackathon,
28 August – 4 September 2026. Everything here runs on an Alpaca **paper**
account. No real money is involved at any point.

> **Status: building.** The design is finished and committed under
> [`docs/`](docs/). No strategy has been measured yet, and nothing has been
> approved to trade. This line changes when that changes.

## What it does, in one paragraph

Every minute the US stock market is open, the agent looks at the price of SPY —
a fund that holds the 500 largest American companies, and the most heavily
traded thing on the market. A small, fixed rule reads that price history and
either forms an opinion ("this looks cheap relative to where it has traded
today") or says nothing. If it forms an opinion, a second piece of code turns
that opinion into a specific **option contract** to buy — a contract giving the
right to buy or sell shares at a fixed price before a fixed date. A risk layer
then checks the order against hard limits and can only ever shrink it or refuse
it. Whatever happens, the agent writes down what it saw, what it concluded, and
why — including the roughly 1,900 minutes a week it decides to do nothing.

## The part that matters most

**A result nobody can independently check does not exist.** So this repository
records every decision, not just the profitable ones, and publishes the numbers
the rule was looking at when it made them. Anyone can recompute the rule from
the record and confirm the agent did what it claimed.

That principle is not decoration. It comes from a research programme in a
previous repository where a headline finding — "no signal in 52 of 52 tests" —
had to be publicly retracted, because the statistical rule producing it was
broken and rejected its own positive control. The lesson was cheap to write down
and expensive to learn: **a negative result is not a finding until you have
proved you asked the question correctly.**

## Design documents

Read these in order. They are written for someone with no background in trading
or statistics; every term is explained where it first appears.

| Document | What it settles |
| --- | --- |
| [`docs/requirements.md`](docs/requirements.md) | What the competition demands, and what is still open |
| [`docs/design.md`](docs/design.md) | The whole system: the pieces, the contracts between them, the risk limits, the journal, the dashboard |
| [`docs/options_data.md`](docs/options_data.md) | What we measured about Alpaca's data, with the exact commands and raw answers |
| [`docs/strategy_candidates.md`](docs/strategy_candidates.md) | The two candidate strategies and the pass/fail rule, fixed **before** any result was read |

## Repository layout

```
src/agent/      the trading system  (strategy, expression, risk, journal, drivers)
scripts/        data downloads, the live quote recorder, the analysis runners
tests/          the guards: no look-ahead, no forbidden imports, no risk-limit growth
dashboard/      the public page and the question box
supabase/       the journal's table definitions
docs/           the design, and the evidence behind it
```

## Two things that are deliberately absent

**No API key appears anywhere in this repository**, in any form, and no page in
the dashboard has a field that accepts one. There is no code path from the
public question box to the broker — it is not blocked, it is absent.

**The trading rule is not an AI.** It is a small deterministic rule: given the
same prices it always does the same thing. The AI sits in the explanation layer,
answering questions about the recorded rows. The trading is autonomous and
auditable; the AI makes it explicable.

## Licence

MIT. See [`LICENSE`](LICENSE).
