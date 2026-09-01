# An autonomous options trading agent, and a record of everything it decided

Built for the lablab.ai × Alpaca **Options Alpha Agents** hackathon,
28 August – 4 September 2026. Everything here runs on an Alpaca **paper**
account. No real money is involved at any point.

> **Status: building.** The design is finished. No strategy has been measured
> yet, and nothing has been approved to trade. This line changes when that
> changes.

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

Four documents settle what this is and what would count as it working: what the
competition demands and what is still open; the whole system and the contracts
between its pieces; what we measured about Alpaca's market data, with the exact
commands and the raw answers; and the two candidate strategies with the
pass/fail rule, fixed **before** any result was read.

**They are not published in this repository yet.** They exist and they are
still the authority on what is being built — they are simply not tracked here
during the build week. Nothing in the code depends on them being present.

## Repository layout

```
src/agent/       the trading system  (strategy, expression, risk, journal, drivers)
scripts/         data downloads, the live quote recorder, the analysis runners
tests/           the guards: no look-ahead, no forbidden imports, no risk-limit growth
dashboard/       the public page, and the session it draws
journal/         what the agent actually decided, one line per minute
journal_example/ an invented session, clearly labelled, for developing the page
results/         backtests: the trades and the summaries, with their input hashes
.github/         the workflow that publishes the dashboard
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
