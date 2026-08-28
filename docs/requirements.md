# Requirements — what the organisers actually asked for

Source: the mission email received 2026-08-28. This file is the authority. Where
anything in `design.md` disagrees with this page, this page wins.

---

## The mission, as written

Build autonomous AI trading agents on Alpaca — a programmable brokerage where an
API key lets an application place orders on US stocks, options, ETFs and crypto.

The main challenge is **Options Alpha Agents**: build an autonomous agent
designed to generate profit and loss. Develop a clear, testable strategy and show
how the agent spots opportunities, makes decisions, manages positions, and
performs over the competition window.

Everything runs in Alpaca's paper trading environment. No real capital, no
funding, but a live market to build against.

## The three hard requirements

Every project must do all three.

| # | Requirement | Where we stand |
| --- | --- | --- |
| 1 | **Autonomous agents** built on Alpaca's Trading API | The design covers this. The agent runs unattended through the trading day. |
| 2 | **Must use Alpaca's MCP server or its CLI tools** | Already satisfied. `hackathon/scripts/download_bars.py` drives the Alpaca CLI, and the MCP server is configured in `.mcp.json`. |
| 3 | **All strategies must incorporate options trading** | **Not satisfied.** Every design decision so far assumes one-minute bars on the SPY stock. See "What this breaks" below. |

## The account rules

Read this section twice — it disqualifies projects, not just points.

- While building, any paper account is fine.
- **For the final submission, a brand-new Alpaca paper trading account dedicated
  to this hackathon is required.** A project run on an existing or reused account
  **is not eligible for judging**.
- The competition account's starting balance must be set to **$100,000**.
- The **paper account ID must be included in the submission**. This is how judges
  evaluate profit and loss.

**Action, and it belongs to Sami, not to Claude:** create the new paper account
now, so the trading history is clean from the first minute. Claude does not
create accounts and does not handle the keys.

Until that account exists, no trading run counts for anything. Anything placed
before it is prototyping.

## The submission

Alongside the artifacts already planned — public repository, live application,
video, slides, cover image — one more is now explicit:

- **A one-page write-up** covering the AI logic, the risk gates, and how Alpaca's
  infrastructure was used.

The design already produces all three of those as a by-product. Sections 2 and 4
are the AI logic and the risk gates; the CLI, the MCP server and the Trading API
are the infrastructure.

---

## Correction to an earlier assumption

Earlier planning in this project stated that profit was not judged, on the basis
of the four published criteria (application of technology, presentation, business
value, originality).

**That was wrong.** The mission says the agent is "designed to generate P&L",
and the account ID is collected specifically so that judges can evaluate it.
Profit and loss is judged.

This does not make the honesty machinery in `design.md` section 3 pointless — a
result nobody can verify is still worth nothing, and a blown-up account is not a
good outcome either. But it does change the balance. A system that trades
carefully and finishes flat is no longer automatically a strong submission.

---

## What this breaks

**Options trading is mandatory, and nothing designed so far involves options.**

An **option** is not a share. It is a contract giving the right, but not the
obligation, to buy or sell 100 shares of something at a fixed price before a
fixed date. Buying the right to buy is a **call**; buying the right to sell is a
**put**. The price you pay for the contract is the **premium**, the fixed price
is the **strike**, and the date it dies is the **expiry**.

They behave nothing like shares:

- Their value depends on the share price, the time remaining, and how violently
  the market expects the price to move. A share only depends on the share price.
- They decay. An option loses value every day simply because there is less time
  left. Hold one long enough and it goes to zero on its own.
- One contract covers 100 shares, so position sizing works differently.
- They are far less heavily traded than the shares, so the gap between the buy
  and sell price is much wider — the trading cost per round trip is a different
  order of magnitude from SPY stock.

The consequences for what has already been designed:

| Part of the design | Status |
| --- | --- |
| Section 1, the six components | Survives unchanged. |
| Section 2, the strategy contract | Survives, with one amendment: the intent must name an option contract, not just a stock symbol. |
| Section 3, the backtest driver | **Hit hardest.** Backtesting options needs historical option prices, and the fill and cost model has to be rebuilt around much wider spreads. Whether the free tier provides usable option history is unverified. |
| Section 4, the live trader | Survives structurally. The risk limits need rewriting for options, where a position can lose its entire value. |
| `strategy_candidates.md` | Now partly obsolete. It specifies a stock strategy. |

## Open questions this creates

1. ~~Does the free Alpaca tier provide historical options data good enough to
   backtest against?~~ **Answered 2026-08-28: yes.** Minute-by-minute history
   back to January 2024, delayed by exactly 15 minutes, no greeks. Measurements
   and commands in `options_data.md`. Section 3 is no longer blocked, but its
   cost model has to be rebuilt — option spreads are hundreds to thousands of
   times wider than SPY's, relative to the money at risk.
2. ~~Does the signal stay on the shares, or does the strategy read option
   prices directly?~~ **Answered 2026-08-28: the signal stays on the shares.**
   Direction is decided from stock prices and expressed by buying an option.
   `design.md` section 2A is the design; section 4 covers what the fifteen-minute
   delay still costs us at the moment of ordering, which is more than the first
   draft admitted.
3. ~~What replaces the stock risk limits?~~ **Answered 2026-08-28**, with
   numbers, in `design.md` section 4: at most $1,000 of premium per position,
   two positions at once, stop for the day at −$2,000, flat by 15:45 New York
   time, buy-only. **Approved by Sami on 2026-08-28.**
4. ~~Where exactly does the AI sit?~~ **Answered 2026-08-28: in the explanation
   layer, not the trading.** The trading rule is deterministic on purpose —
   that is what makes it backtestable, reproducible and checkable by someone
   else. The AI is the public ask box, which reads the system's own recorded
   decisions and answers questions about them in plain English. The
   *operation* is fully autonomous: nobody touches it for four sessions.
   Reasoning in `design.md` section 6.

A new open item takes their place: **seven assumptions this design depended on
had never been measured.** Five were measured on 2026-08-28 and one of them
changed the design — there is no historical bid and ask for options, so the
backtest has to model the largest cost it faces rather than read it. Two remain,
both requiring an order to be placed, and both are Sami's to run. The full
register, with what each result changed, is in `design.md` section 8.
