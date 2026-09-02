# An autonomous options agent, and the record of everything it decided

**Submission write-up — lablab.ai × Alpaca, Options Alpha Agents.**
Paper account **`PA3JTED9VTZY`**. Live record:
<https://samisuleman10.github.io/alpaca-trading-bot-hackathon/>

Every minute the US market is open, this agent looks at the price of SPY — a
fund holding the 500 largest American companies — decides whether to buy an
**option contract** on it, and writes down what it decided and why. An option
is a contract giving the right to buy shares at a fixed price before a fixed
date; you can only lose what you paid for it, which is why the agent buys them
and never sells them. Most minutes it decides to do nothing, and **the minutes
it did nothing are recorded exactly as carefully as the minutes it traded.**
That is the point of the project.

---

## 1. The AI logic

The agent runs a fixed decision loop, once per closed minute, in this order:

1. **Fetch the minute that just closed.** If it has not arrived within twenty
   seconds, skip the minute and journal the skip. A late bar is missing data,
   never a reason to guess.
2. **If a position is open, check the exits first.** Take-profit, stop-loss, or
   held-too-long. If one fires, sell and stop — no new entry is considered that
   minute.
3. **Only if flat, form a view.** The rule compares the current price to
   **VWAP** — the day's average price so far, weighted by how much traded at
   each level, i.e. the price the typical dollar paid today. If price is more
   than 0.5% below it *and* this minute traded at least 20% more than usual,
   the rule says the share looks temporarily cheap.
4. **Turn the view into a contract.** Pick the strike nearest the current price
   expiring tomorrow, size the trade on the **premium** — the money actually at
   risk, not the ~$77,000 of shares one contract controls.
5. **Refuse if the trade cannot pay for itself.** Every option has a gap
   between its buying and selling price, and that gap is paid twice on a round
   trip. If it exceeds 5% of the contract's value, the agent declines and
   journals the numbers. The whole hoped-for gain is 0.4%, so a 6% gap is not a
   worse trade — it is one that cannot win.

Two structural guarantees sit under this. The strategy module is **forbidden by
an automated test from importing anything outside Python's standard library** —
no network, no clock, no account — so it cannot accidentally read the future or
depend on live state. And a **look-ahead guard** makes reading any bar past the
current minute raise an error rather than return a number; a test deliberately
reaches into the future and asserts the crash.

**No language model is in the trading path.** The decision rule is small, fixed,
and reproducible from the record; an LLM sitting between the market and an order
would make every decision unauditable for no measured gain.

## 2. The risk gates

A separate layer checks every intended order and **can only ever say *no* or
*smaller*. It has no path that makes anything larger.** That is not a claim in a
comment: a property test throws 5,000 randomly-built orders at it across random
limits, times, position counts and losses, and asserts that not one comes back
bigger than it went in. A future change that quietly adds a growth path fails
that test without anyone having to anticipate it.

The limits in force this week, at the pre-committed **quarter size** (see §4):

| Gate | Limit | Why |
| --- | --- | --- |
| Most spent on one trade | **$250** | 0.25% of the account |
| Positions open at once | **1** | so at most $250 is exposed |
| Stop for the day after losing | **$500** | this can prevent a recovery; it stays anyway |
| No new positions after | **15:40 NY** | nothing opened that cannot be closed |
| Everything closed by | **15:45 NY** | no position is carried overnight |
| Selling options we do not own | **impossible** | a sold option has no upper bound on the loss |

A **separate process on its own schedule** sweeps at 15:50 and closes anything
still open, without asking whether the trader is healthy — because the case
that matters is the one where it is not. It calls, verifies, and calls again: a
`200` from a close request means *accepted*, not *closed*.

## 3. The Alpaca infrastructure

One file, `broker.py`, is allowed to talk to Alpaca; nothing else imports
`subprocess` or touches the network. It drives the **Alpaca CLI** for the market
clock, minute bars, the option chain, live option quotes, account equity,
orders, and the flatten-everything calls.

Three things we measured rather than assumed:

- **The account is proved before every order.** `alpaca doctor` must report the
  paper endpoint. Anything else, including no answer, stops the order.
- **`--profile` is silently ignored by `doctor`.** So a run could check one
  account and trade on another, with the check passing and meaning nothing. The
  account is selected once per process by environment variable, and the trader
  refuses to start unless the account it is actually connected to is
  `PA3JTED9VTZY`.
- **We do not wrap the CLI in our own retry loop.** It already retries rate
  limits three times and honours `Retry-After`; a second layer multiplies the
  two and stalls past the minute.

**On data:** SIP is the consolidated feed carrying all US volume, but on this
subscription it arrives 15 minutes late — fine for backtests, useless live. IEX
is real-time to the second but carries ~4% of volume. The backtests ran on SIP
and the live agent reads IEX, so we ran the entire study **on both feeds** to
find out whether that gap changes the answer. It does not (§4).

## 4. What we measured, and what it says

The entry rule was tested across **144 combinations** of four settings against a
**coin-flip control**: identical stops, targets and exits, with entry timing
chosen at random. If a rule cannot beat random entry, it has no edge — it is
just holding the market.

| | SIP | IEX |
| --- | --- | --- |
| Settings with enough trades to read | 96 | 96 |
| Beat their own coin flip at all | 18 | 30 |
| **Survive having asked 96 questions** | **0** | **0** |

**Zero, on both feeds, with trading costs set to zero** — the most generous test
possible. The best contiguous block of settings gives +0.40 basis points per
trade over 1,727 trades (t = +0.95, p = 0.343): indistinguishable from luck.

The plan fixed the response to this **before** any result was read: run the
tested rule at quarter size, one position, and publish both numbers. That is
what is running. A fallback chosen after seeing the result is not a fallback,
it is a rationalisation.

**So the live P&L this week is a sample of a rule we have already shown we
cannot distinguish from chance.** Whatever it comes out at, up or down, one week
of trading cannot overturn that — and the agent is small enough, and its record
complete enough, that anyone can check the claim themselves.

## 5. Known gaps, stated rather than hidden

- **No option-level backtest.** 2.4 GB of option prices were downloaded and not
  used. There is no historical bid/ask on any data tier, so the cost of trading
  would have to be modelled rather than measured, and modelling it more
  precisely for a rule already shown to have no edge was not the best use of
  three days.
- **No independent auditor.** The repo's standing rule is that the code checking
  the rules should be written by someone who never saw the rules. One author,
  one week: suspended knowingly. The per-minute record still lets someone else
  check the work — nobody has.
- **The strategy's thresholds are not ours.** The entry distance, volume ratio
  and 15-minute exit came from the competition brief with no stated provenance.
  We swept them all rather than trusting them, which is how we found they do
  not work.

---

*62 automated tests pass on every commit, and the public page is only
republished from a commit where they did. The dashboard is a static file built
from the journal on disk — there is no database between the record and the page
that could disagree with it, and no field anywhere that accepts an API key.*
