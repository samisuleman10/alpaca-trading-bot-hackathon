# What options data we can actually get

Measured on 2026-08-28 against the paper account, using the Alpaca CLI. Every
number below came from a real request, not from documentation. The commands are
listed at the bottom so anyone can re-run them.

This file answers open question 1 in `requirements.md`: *does the free tier
provide options history good enough to backtest against?*

**Yes.** Comfortably. This was the risk that could have sunk the whole plan, and
it did not.

---

## The short version

| Question | Answer |
| --- | --- |
| Can the account trade options at all? | Yes — approved at **level 3**. |
| Can we list option contracts? | Yes, live and long-expired ones. |
| Is there minute-by-minute price history? | Yes, back to **18 January 2024**. |
| How fresh is it? | **Trades and bars** older than 15 minutes are free; the last 15 minutes is blocked. **Bid and ask prices are real-time** — corrected 2026-08-28, see the second round below. |
| Is there historical bid/ask? | **No.** The endpoint does not exist. This is the finding that reshapes the cost model. |
| Do we get the risk numbers (the "greeks")? | **No.** They come back as zeros. |

---

## 1. The account can trade options

```
options_approved_level: 3
options_trading_level:  3
options_buying_power:   $100,000
equity:                 $100,000
```

Alpaca grades options permission in levels. Level 3 is the one that allows
buying calls and puts outright *and* combining two contracts into a **spread**
— buying one and selling another to cap both the profit and the loss. Level 4,
which we do not have, allows selling contracts you have not covered, where the
loss has no ceiling. Not having level 4 is a safety rail, not a limitation.

Note this is the *old* practice account. The competition account Sami still has
to create will need checking for the same level.

## 2. Contracts are listable, including dead ones

An option contract has a machine name that packs everything into one string:

```
SPY 260828 C 00770000
 |     |    |     |
 |     |    |     strike price: $770.00
 |     |    call (P would be put)
 |     expires 28 August 2026
 the shares it is a claim on
```

We can list every contract for a stock, filtered by expiry date, strike range,
and call-versus-put. Crucially we can also list **expired** contracts — that is
what makes backtesting possible at all, because a backtest of last March needs
the contracts that existed last March, and all of them are dead now.

Verified: SPY contracts that expired on 15 March 2024 still list fine.

SPY currently expires contracts **every trading day**, so there is always a
contract with hours of life left in it. Those are called **0DTE** — zero days to
expiry. They are the most heavily traded options in the world.

## 3. Price history exists, minute by minute

For one at-the-money SPY call expiring the same day, one session produced
**405 one-minute bars**. A trading session is 390 minutes, so that contract
traded in essentially every minute of the day plus some of the extended hours.
This is not a thin, gappy instrument — it is one of the busiest things on the
market.

Working: 1-minute bars, 5-minute bars, daily bars, and the raw individual
trades. Depth goes back to **2024-01-18**. Contracts that died before that date
return nothing.

That is about two and a half years — enough to build a real backtest on, and
more than the one year of SPY stock data already downloaded.

## 4. The fifteen-minute wall, measured

Asking for option bars ending *right now* fails with a 403 error and the message
`OPRA agreement is not signed`. OPRA is the Options Price Reporting Authority,
the central tape that collects every options trade in the US. Reading it live
costs money; reading it late is free.

The boundary was measured rather than guessed. With the clock at 10:18:48Z, an
end time of 10:05:00Z was refused and 09:00:00Z was allowed. Fifteen seconds
later, 10:03:00Z had become allowed. The cutoff moves with the wall clock and
sits at **exactly 15 minutes**.

This is the same rule as the stock feed, and it has the same consequence,
already recorded in `design.md`:

- **Backtesting: unaffected.** Historical data is complete.
- **Live trading: the agent is looking at a 15-minute-old picture** unless we pay.

That is a design constraint, not a bug, and the strategy has to be one that
still makes sense on delayed information. A strategy that needs to react within
seconds is off the table for this project.

## 5. What we do NOT get: the greeks

Every chain and snapshot request returns the **greeks** — the standard set of
numbers describing how an option's price responds to things — as all zeros, and
implied volatility as empty.

The one that matters most is **delta**: how much the option's price moves for a
one-dollar move in the underlying shares. A delta of 0.5 means the option gains
about fifty cents when the stock gains a dollar. It is the normal way to size an
options position and the normal way to pick a strike.

We do not get it. Two consequences:

- If the strategy needs delta, we compute it ourselves from the standard option
  pricing formula, using the price, the strike, the time left, and the interest
  rate. This is textbook arithmetic, not research — but it is work, and it is
  work that has to be right.
- Whether the greeks are simply not served on this tier, or are only populated
  while the market is open, is **not yet established**. Every probe here ran
  before the opening bell. Worth one re-check during market hours before
  assuming we have to build our own.

## 6. What a round trip costs

The gap between the price you can sell at and the price you can buy at is the
**spread**, and you pay it. Measured on SPY calls expiring the same day, at the
last quotes before yesterday's close:

| Strike | Bid | Ask | Spread | As a share of the price |
| --- | --- | --- | --- | --- |
| 768 | 4.26 | 4.31 | 0.05 | 1.2% |
| 769 | 3.51 | 3.65 | 0.14 | 3.9% |
| 770 | 2.86 | 2.87 | 0.01 | 0.3% |
| 771 | 2.30 | 2.33 | 0.03 | 1.3% |
| 772 | 1.77 | 1.78 | 0.01 | 0.6% |

For comparison, SPY shares trade at $770.83 with a one-cent spread — **0.001%**.

So per round trip, an option costs somewhere between a few hundred and a few
thousand times more than the shares, measured against the money you put in. That
is the single most important number in this file for section 3: the cost model
built for stocks is off by orders of magnitude and cannot be reused as-is.

The spreads are also **erratic** — 0.3% and 3.9% sit two strikes apart. Which
contract you pick is itself a cost decision, and the backtest has to model that
per-contract rather than as one flat number.

---

## Commands used

Run from anywhere with the Alpaca CLI authenticated. All read-only.

```bash
alpaca account get --jq '{options_approved_level, options_buying_power}'
alpaca option contracts --underlying-symbols SPY --expiration-date-gte 2026-08-28 --limit 5
alpaca option contracts --underlying-symbols SPY --status inactive --expiration-date 2024-03-15 --type call
alpaca data option bars --symbols SPY260828C00770000 --timeframe 1Min --start 2026-08-27 --limit 10000
alpaca data option trades --symbols SPY260828C00770000 --start 2026-08-27 --limit 3
alpaca data option chain --underlying-symbol SPY --expiration-date 2026-08-28 --type call --strike-price-gte 768 --strike-price-lte 774
```

Note the trap: passing `--end` as a bare date such as `2026-08-28` is read as
the *end* of that day, which is in the future, and the request is refused. Leave
`--end` off, or give it an explicit timestamp at least 15 minutes in the past.

---

# Second round of probes — 2026-08-28, market open

The first round above ran before the opening bell, which left two questions
unanswerable and one answer wrong. This round ran between **09:53 and 09:58 New
York time with the market open**, and it overturned a claim in the summary
table. That claim is corrected in place, and the correction is the most
important thing on this page.

Everything here answers the register in `design.md` section 8.

## The short version of the second round

| Probe | Question | Answer |
| --- | --- | --- |
| 7 | Are option bid/ask prices delayed during market hours? | **No. They are real-time to the second.** The 15-minute wall applies to trades and bars, not quotes |
| 1 | Is there *historical* bid/ask for expired contracts? | **No. The endpoint does not exist.** Not a tier limit — Alpaca does not offer it |
| 6 | Are the greeks zero only because the market was shut? | No. Zero during trading hours too. Permanently absent |
| 5 | Is the account approved to buy options? | Yes, level 3, $100,000 |
| 4 | Can a year of option history be downloaded in reasonable time? | Yes, comfortably. No rate limiting hit, and one request can carry many contracts |
| 3 | Does the close-everything call work on an option position? | **Yes** — run by Sami. But a success means *accepted*, not *closed* |
| 2 | Do stop orders work on options? | **Not run.** Requires submitting an order, and nothing depends on the answer |

Probes 7 and 1 together change the design, in opposite directions.

## Probe 7 — option quotes are real-time

Three samples, twelve seconds apart, on a same-day SPY call struck at $772
(SPY was trading at $771.90):

| Wall clock | Newest quote | Newest trade | Newest minute bar | Bid / ask |
| --- | --- | --- | --- | --- |
| 13:54:29Z | 13:54:30Z | 13:39:30Z | 13:39:00Z | 1.63 / 1.68 |
| 13:54:42Z | 13:54:43Z | 13:39:42Z | 13:39:00Z | 1.68 / 1.69 |
| 13:54:55Z | 13:54:56Z | 13:39:56Z | 13:39:00Z | 1.62 / 1.63 |

The quote timestamp tracks the wall clock **to the second**. The trade
timestamp trails it by exactly fifteen minutes. The minute bar does not move at
all, because the most recent bar we may see is also fifteen minutes old.

So the fifteen-minute wall is real, and it applies to **what has traded**. It
does not apply to **what is currently on offer**.

This is better news than the design assumed. The spread check and the price we
put on an order both need the current bid and ask, and we have them, live, free,
from the opening bell. The design's rule barring entries before 09:45 exists
only because we believed otherwise, and it can go.

Note also how much the spread moved in twenty-six seconds: one cent wide, then
one, then five and back. On a contract priced at $1.63 a five-cent spread is
**3%** of the money at risk, paid on the way in and again on the way out. That
is the entire reason the spread check exists, and it now has a live number to
check against.

## Probe 1 — there is no historical bid/ask, at all

```
GET https://data.alpaca.markets/v1beta1/options/quotes/latest   ->  200
GET https://data.alpaca.markets/v1beta1/options/quotes          ->  404 Not Found
GET https://data.alpaca.markets/v1beta1/options/trades          ->  200
```

The historical trades endpoint works. The historical quotes endpoint returns
**404 — it does not exist**. This is not a permissions error and not a free-tier
restriction; there is no such endpoint to be given access to. Compare the
403 "OPRA agreement is not signed" we saw in the first round, which is what a
permissions problem actually looks like.

**What this costs us.** The backtest cannot know what a contract's bid and ask
were at any past moment. It can know every price at which the contract actually
*traded*, minute by minute, and the open, high, low, close and volume of each
minute. But the gap between buy and sell price — the single largest cost in
options trading, and the thing the whole cost model is about — is not recorded
anywhere we can reach.

**The consequence, stated plainly: the live system will have better information
than the rehearsal that validated it.** That is an unusual and uncomfortable
shape. It is the reverse of the normal danger, and it needs saying out loud
rather than being quietly enjoyed.

**What replaces it** is spelled out in `design.md` section 3: a spread *model*
built from the two things we do have — the scatter of traded prices inside a
minute, which bounces between the unseen bid and ask, and live quotes we start
recording today. The model is then validated against the live record during the
competition sessions, which is the one place both numbers exist at once.

Every backtest result stays reported at zero cost, at the model's estimate, and
at double it. That triple was already committed; it now does real work rather
than being a formality.

## Probe 6 — the greeks are permanently zero

The first round could not tell whether the zeros were real or an artifact of the
market being shut. They are real:

```
"greeks": { "delta": 0, "gamma": 0, "rho": 0, "theta": 0, "vega": 0 }
```

taken at 13:54Z with the market open and the contract actively quoted. Nothing
in the design depends on them. Had they been available, `express` would have had
a better way of choosing a strike than distance from the current share price.

## Probe 4 — bulk downloading is not a problem

Twenty sequential requests for a full day of one-minute bars, one contract each:
**14.8 seconds, all 200, no rate limiting.** Roughly three quarters of a second
per request, most of which is the command-line tool starting up rather than the
network.

Then the lever that actually matters: **one request can carry many contracts.**
Thirty contract symbols, a full day of one-minute bars, **0.96 seconds**.

A year of history for the contracts a sweep would consider is therefore minutes
of work, not hours. Paging has to be handled — a large request comes back with a
token for the next page — but nothing here threatens the schedule.

## Probe 3 — the flattener's one call works, with a catch

Run by Sami on 2026-08-28 at 16:05 UTC, because it means placing orders and
those are his to place. He bought one SPY call struck at $786 expiring 31 August
— chosen because it was worth two cents, and because a contract expiring *today*
left open by a failed close is the exact accident being tested for.

With that position open, `alpaca position close-all --cancel-orders` returned:

```
"status": 200,
"position_intent": "sell_to_close",
"order_type": "market",
"filled_qty": "0",
"status": "pending_new"
```

and the position list a moment later was empty. Looking the order up afterwards
shows why:

```
"submitted_at": "2026-08-28T16:05:01.849Z"
"filled_at":    "2026-08-28T16:05:01.881Z"
"filled_avg_price": "0.01",  "filled_qty": "1",  "status": "filled"
```

**Thirty-seven milliseconds.** So the call works on options, and it works fast.

**But read what came back at the moment of the call.** The 200 arrived while the
order was still `pending_new` with nothing filled. The response is a receipt for
*submitting* a sell order, not a confirmation that the position is gone. It
filled instantly here because the contract was worth a penny, nobody wanted it,
and the market was calm. None of those things is guaranteed at 15:50 on a day
when something has already gone wrong — which is the only day the flattener
runs for real.

**What that changes:** the flattener closes, then re-reads its positions, and
retries if the list is not empty, a small fixed number of times, and journals
what it found either way. `design.md` Difference 5 carries the specification.
Assuming success from a 200 would have produced a defence that reports victory
while the position is still open, which is worse than no defence, because it
turns a loud failure into a silent one.

**One good thing for free:** Alpaca stamped the order `position_intent:
sell_to_close` without being asked. We never have to distinguish selling
something we own from selling something we do not — the latter being the one
thing the risk rules forbid outright.

**Cost of the whole test: one dollar of pretend money.** Bought at $0.02, sold
at $0.01, one contract, on a paper account.

## Probe 2 — not run, and why

It requires submitting a stop order on an option, and placing orders is Sami's
to do, not mine, even on a paper account where the money is simulated.

Probe 2 no longer matters — the design stopped depending on broker-side stops
when the independent flattener replaced them, so the answer would change
nothing. It stays on the list only so that nobody later assumes it was checked.

## Commands used, second round

```bash
# probe 3, run by Sami
alpaca order submit --symbol SPY260831C00786000 --qty 1 --side buy \n  --type limit --limit-price 0.06 --time-in-force day
alpaca position list
alpaca position close-all --cancel-orders
alpaca position list
alpaca order get --order-id <the id close-all returned>

# everything else, read-only
alpaca clock --quiet
alpaca account get --quiet --jq '{options_approved_level, equity, status}'
alpaca data latest-quote --symbol SPY --feed iex --quiet
alpaca data option snapshot --symbols SPY260828C00772000 --quiet
alpaca data option bars --symbols SPY260827C00770000 --timeframe 1Min \
  --start 2026-08-27T13:30:00Z --end 2026-08-27T20:00:00Z --quiet --verbose
alpaca api GET /v1beta1/options/quotes --use-data-api \
  --query "symbols=SPY260827C00770000&start=...&end=..." --quiet
```

**Two traps worth recording**, both of which cost time:

- On Git Bash, a path beginning with `/` is silently rewritten into a Windows
  path before the command sees it, so `alpaca api GET /v1beta1/...` requested
  `https://data.alpaca.markets/C:/Program Files/Git/v1beta1/...` and returned a
  404 that looked like a missing endpoint. Prefix the command with
  `MSYS_NO_PATHCONV=1`. **A 404 is not evidence until the URL has been printed**
  — `--verbose` prints it.
- `alpaca api` talks to the trading host by default. Data endpoints need
  `--use-data-api`, or they 404 as well.

Both of those produced a 404 identical in appearance to the real one in probe 1.
The real one was only believable after `--verbose` showed the correct URL going
to the correct host, and after the neighbouring `/trades` endpoint returned 200
from that same URL prefix.
