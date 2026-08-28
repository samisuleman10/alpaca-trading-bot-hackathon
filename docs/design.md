# Design — Alpaca hackathon trading system

| | |
| --- | --- |
| **Status** | **Design complete**, revised after an independent review and again after the section 8 probes were run on 2026-08-28. Outstanding: building section 3's cost model, and re-checking the account approval level on the competition account. |
| **Phase** | Design. **No implementation has been approved.** |
| **Started** | 2026-08-28 |
| **Submission deadline** | 2026-09-04 |
| **Where it ships** | A fresh public repository, not `ai-trade`. |

---

> **Requirements changed on 2026-08-28.** The organisers' mission email adds
> three things this document does not yet reflect: **options trading is
> mandatory**, **profit and loss is judged**, and the submission must run on a
> **brand-new paper account** funded with $100,000 whose ID is submitted.
> `requirements.md` is now the authority on what is required, and it lists
> exactly which sections below survive and which do not. Sections 1, 2 and 4
> stand with amendments. Section 3 needs rework.

---

## What this document is

The plan for what we are building, how the pieces fit together, and which
technologies we picked and why. It is written to be read start to finish by
someone who has never opened a brokerage account.

It is **not** an implementation plan. Nothing here says which file to write
first. That comes afterwards, as a separate document, and only once this one is
approved.

Two words worth fixing before anything else, because the whole design turns on
them:

- A **backtest** is a rehearsal. You take a year of past prices, feed them to
  your trading rules minute by minute, and record what would have happened. It
  is the only evidence you will ever have that the rules are worth running at
  all.
- The **live trader** is the real thing. Same rules, but the prices arrive one
  at a time as the day actually happens, and the orders get sent to a broker.
  Ours go to a **paper** account — Alpaca simulates the money, so nothing real
  is ever at stake.

The danger sitting under every trading project is that these two are separate
programs which are *supposed* to behave identically and quietly do not. That is
how most people's backtests end up lying to them. Nearly every decision below
exists to make that lie hard to tell.

---

## Decisions already fixed

These were settled before this document was started. They are inputs to the
design, not open questions.

| Decision | Choice | Why |
| --- | --- | --- |
| What we submit | A dashboard showing what the system did, plus a public box where a visitor can ask about it in plain English | Four of the five required artifacts are the explanation rather than the trading: the dashboard, the write-up, the video and the slides. **Profit and loss is judged too** — the account ID is submitted for exactly that purpose — so presentation is most of the score, not all of it. |
| Backtest and live trader | **One shared engine, two drivers** | So we can prove the thing we tested is literally the thing that traded. The alternative — two programs — drifts silently and invalidates the evidence. |
| Trader language | Python | It is where our data tooling and existing research harness already are. |
| Dashboard | Next.js, hosted on Vercel | Standard, free tier, and the deployment is one command. |
| Where they meet | Supabase — a hosted PostgreSQL database | The trader runs on a private server and the dashboard runs on Vercel; neither can see the other's filesystem, so they need one shared table. Supabase is the boring choice: someone else runs Postgres for us, the free tier is far larger than our ~2,500 rows, and if we ever leave, it is just Postgres and the data comes with us. |
| Development order | Everything runs locally first | Nothing may depend on being deployed. Same code, same behaviour, laptop or server — only environment variables differ. |
| Money | Paper account only | Nothing here touches real funds, at any point, under any circumstance. |

**One item to verify, not assume:** Supabase is believed to pause free projects
after a stretch of inactivity. Irrelevant during the build week, but it would
matter if a judge opened the dashboard two weeks after submission and found it
empty. To be checked. If true, the fix is small — keep a copy of the final
journal as a static file in the repository so the page always has something to
show.

---

## The work, end to end

Eight phases, in order. Purple is where we are now. Amber is the only place the
path can bend.

![The eight build phases, from design through to submission](diagrams/phase-flow.png)

Two things about this shape are worth stating plainly.

**The decision gate is real, not decorative — but it is a *sizing* gate, and
calling it anything grander would be dishonest.** Section 7 pre-commits to
trading a failing candidate at reduced size, so the gate does not decide
*whether* we trade. It decides **how big, and what we claim**. After the
backtest we will have a number, and that number is allowed to say the strategy
does not work. Costs are
the reason. Every trade pays a **spread** — the small gap between the price you
can buy at and the price you can sell at, which you lose the instant you enter —
plus a little **slippage**, meaning the price moved between deciding and being
filled. A rule that looks profitable on paper very often turns out to be paying
all of its profit away in exactly those two places.

If that is the answer, we do not quietly proceed anyway — and we do not
pretend the answer does not matter either. Profit and loss is judged, so
"we tested this and the edge was not there" is a necessary thing to say and not
a sufficient thing to submit. **Section 7 pre-commits to what happens instead**,
before we know which way the number falls: run the tested candidate at reduced
size, and publish both the negative test and the live record beside it.

Reporting the honest result is still the non-negotiable part. A curve nobody can
trust scores nothing on the criteria that reward engineering and explanation,
which are most of them.

**Everything below the gate depends on it, and that is the schedule risk.** The
backtest is the only phase whose result cannot be predicted. If it takes two
days instead of one, four phases slide. That is much of why the planning phase
exists at all — so we know in advance which work can proceed in parallel with
the research rather than waiting behind it. The dashboard, for instance, does
not need to know which strategy won.

---

## Section 1 — The pieces and how they fit

Six pieces. Each has one job, and each can be understood without reading the
others.

```
                    ┌─────────────────────┐
  historical CSV ──▶│                     │
   (already have)   │   BACKTEST DRIVER   │──▶ results + charts
                    │   (Python, laptop)  │
                    └──────────┬──────────┘
                               │  calls
                               ▼
                    ╔═════════════════════╗
                    ║  STRATEGY FUNCTION  ║   ← the only copy of the rules
                    ║   bars ──▶ intent   ║      imports nothing
                    ╚═════════════════════╝
                               ▲
                               │  calls
                    ┌──────────┴──────────┐
  Alpaca bars ─────▶│    LIVE TRADER      │──▶ orders to Alpaca (paper)
   (real time)      │   (Python, server)  │──▶ journal rows
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │      SUPABASE       │   ← the only thing both sides touch
                    │  (decision journal) │
                    └──────────┬──────────┘
                               │  reads
                    ┌──────────┴──────────┐
                    │ DASHBOARD + ASK BOX │
                    │  (Next.js, Vercel)  │
                    └─────────────────────┘
```

### 1. The strategy function

The trading rules, and nothing else.

It takes the price history up to right now and returns an **intent** — something
like *buy 40 shares of SPY, give up if the price falls to 585.10, take the
profit at 590.40* — or it returns nothing at all, which is the answer most
minutes.

It does not touch the network, the clock, the account balance, or any file. It
only decides.

This is the piece we protect. It is the only file in the project allowed to hold
a trading opinion, and the only one forbidden from importing anything outside
Python's standard library. A test enforces that literally, by reading the file
and failing the build if a forbidden import appears. This is not a new idea
here — the `ai-trade` audit modules are held to the same rule by the same
mechanism, and it works.

### 2. The backtest driver

Feeds the strategy function a year of historical bars, one minute at a time,
pretends to fill the orders it asks for, and writes down the results. Runs on
your laptop. Never talks to Alpaca.

A **bar** is one minute of trading compressed into five numbers: the price at
the start, the highest, the lowest, the price at the end, and how many shares
changed hands. It is the unit everything in this system consumes.

### 3. The live trader

Runs on the server during market hours. Every minute it fetches the bar that
just closed, hands the history to the strategy function, and if an intent comes
back, passes it to the risk layer and then to Alpaca. Then it writes down what
happened and why.

### 4. The risk layer

Sits between the strategy and the broker, and it can only ever say **no** or
**smaller**. Never yes-and-more. It holds the position size cap, the maximum
number of open positions, the daily loss limit, and the rule that everything is
closed before the closing bell.

It is deliberately separate from the strategy, for two reasons.

The first is that they change for different reasons and on different schedules.
You swap the strategy because the evidence says the rules do not work — a
research decision, made between versions. You tighten the risk because you are
down three percent on the day — an operational decision, which can happen
mid-session without touching a line of strategy code.

The second matters more for this hackathon: keeping them apart means the risk
file is short enough to *read*. Ten or fifteen lines a judge can scan in twenty
seconds and confirm the system genuinely cannot bet the account. Bury the same
rules inside the strategy and nobody can check that claim without reading three
hundred lines of trading logic. Separated, the guarantee is auditable. Merged,
it is only an assertion.

The strategy is allowed to be wrong. It must not be able to be ruinous.

### 5. Supabase — the decision journal

One table of decisions, one of orders, one of daily summaries. The exact columns
are section 5 of this document.

The trader only ever **writes**. The dashboard only ever **reads**. That one-way
rule is what makes the split safe: nothing on the public internet can reach back
into the trading process.

### 6. The dashboard and ask box

A public page showing what the system did and why, plus a text box where a
visitor can ask about it in English. The ask box reads the same journal rows the
page already displays. It cannot place orders and it cannot see any API key. The
security design for it is section 6.

### The rule that ties it together

**The arrows only point downward and outward.**

The backtest and the live trader both *call into* the strategy; the strategy
calls nothing. The trader *writes to* the journal; the journal calls nothing.
The dashboard *reads from* the journal; it reaches nothing else. Nothing in the
diagram loops back.

That is what lets us claim — and demonstrate — that the public page cannot
influence the trading, and that the system which was backtested is the system
which traded.

---

## Section 2 — The strategy contract

This section fixes the shape of the strategy function: what it is allowed to
see, what it hands back, and where its numbers come from. It is the seam
between the trading rules and everything else, so it is the one interface that
has to be right before anything is built on top of it.

![Three inputs feed the decide function, which returns an intent or nothing](diagrams/strategy-contract.png)

### The trap this section is designed around

**Look-ahead bias** is the most common way a backtest lies to you.

Your rules run over a year of stored prices. At 10:31 on some Tuesday the code
asks "what is the average price today?" — and if it accidentally computes that
over the *whole* day's rows, it has just used prices from 14:00 that had not
happened yet. The backtest looks brilliant. Live, it collapses, because at 10:31
the afternoon does not exist.

It is almost never deliberate. It is an off-by-one in an array index. And it is
invisible: the code runs, the numbers look plausible, nothing fails. The
dropped-in system in this repository's `external/` folder has exactly this bug,
which is why none of its results can be cited.

So the goal here is not "be careful". It is to make the mistake structurally
impossible.

### What the strategy sees

**1. Price history, readable only up to now.**

The obvious approach is to hand the strategy a fresh copy of the bars so far,
every minute. That is safe but wasteful: copying a growing list 97,740 times is
minutes of pointless work on every backtest run.

Instead the driver passes a small object holding a *reference* to the whole
array plus one number — the index of the current bar. Reading at or before that
index works. Reading past it **raises an error**: not a warning, not a lint
rule, an exception that stops the run.

That one choice turns look-ahead from an invisible bug into a loud crash, at a
cost of one comparison per read. It is also directly testable — a test
deliberately reaches into the future and asserts that it blows up, so the guard
is proven rather than assumed.

**2. The open position, read-only.**

The strategy is told whether it currently holds anything, at what price, and
since when. It cannot change any of it. This is a fact about the world, like the
price.

**Corrected 2026-08-28.** This used to call the field an overshoot: the first
candidate would enter with a stop and a target attached to the order, and the
broker would close the trade by itself, so nothing needed to know what we held.
That stopped being true when section 2A moved the stop onto the shares while the
money sits in an option. Nothing closes automatically now - section 4 runs the
exit in our own loop - so knowing the current position is required from the
first line of code.

The original reason stands independently, and is worth keeping: a strategy that cannot
see its own position can only ever be a strategy that never manages an exit,
which rules out most of the interesting ones. Leaving the field out saves
nothing today and forces a change to the trader, the backtest driver and every
existing strategy on the day a second one needs it. One read-only field is the
cheapest way to keep the promise.

**3. The parameters.**

Every threshold is passed in, never typed into the rules: how far from the
average price counts as far enough, how large a position is, where the stop
goes.

Three reasons, in increasing order of importance. You can test five settings
without editing code. The journal can record exactly which settings produced a
given decision. And it blocks the worst habit in this field — quietly nudging a
number until the backtest looks good, then reporting the result as though the
number had been chosen in advance. The rule elsewhere in this repository is that
thresholds are swept across a range and a human reads the table; keeping them in
configuration is what makes that possible.

### What the strategy returns

Either nothing — the answer on the overwhelming majority of minutes — or one
**intent**: which symbol, which direction, how much, where the stop goes, where
the target goes.

Alongside that, every intent carries its own reasoning: a short sentence in
plain English, *and* the raw numbers behind it. For example, "price is 0.42%
below the session average and volume is 2.1 times normal", plus those two values
as data.

One warning attached to that example, because it is the exact shape of a trap
section 4 describes in full: **a rule that mentions volume does not mean the
same thing in the backtest as it does live**, because the two use different
price sources. Any volume-based rule has to survive both, or be dropped.

This is not decoration, and it is not for us. It is the foundation of two things
promised elsewhere in this document:

- The dashboard and the ask box can only explain what the strategy chose to
  write down. Without recorded reasoning they degrade into a list of trades.
- It is the audit trail. With the numbers stored as they were at the moment of
  the decision, anyone can recompute the rule from the journal and confirm the
  system did what it claimed. That is what makes "a result nobody can
  independently verify does not exist" an enforceable rule rather than a slogan.

### The shape, concretely

Illustrative only — this describes the interface, not the implementation.

```
decide(bars, position, params) -> View | None

  bars      view over the price history, truncated at the current bar
  position  what we hold right now, or nothing; read-only
  params    every threshold and size, from configuration

  View      direction (up or down), conviction,
            reason (one sentence), evidence (the numbers behind it)
```

**Conviction** needs defining, because an interface field with no agreed
meaning is a bug waiting to happen. It is a number from 0 to 1 saying how
strongly the rule believes its own answer. Only one thing reads it: `express`
scales the position size by it, so a weak signal buys less. The strategy may
always return 1.0 and nothing breaks; the field exists so that a strategy which
*can* distinguish strong from weak has somewhere to say so.

**Amended 2026-08-28.** This function originally returned a finished order —
symbol, size, stop, target — for shares of SPY. The options requirement split
that in two. `decide` now stops at the opinion, and a second function turns the
opinion into a specific contract. Section 2A covers it, and everything else in
this section is unchanged: the same three inputs, the same look-ahead guard, the
same recorded reasoning.

### How a second strategy plugs in

It is another function with the same three inputs and the same output, listed by
name in one small registry.

The trader, the risk layer, the journal and the dashboard never learn its name.
Switching strategies is a configuration change, not a code change. That is the
whole of what "swappable" means here, and it is a consequence of this contract
rather than a separate feature to build.

---

## Section 2A — The expression layer

The competition requires options. Everything designed before 28 August assumed
shares. This section is the join between the two, and it is the direct
consequence of a decision recorded in `requirements.md`: **the signal stays on
the shares, and the view is expressed in options.**

![Stock bars feed decide, which returns a view; the view and the option chain feed express, which returns one contract or declines](diagrams/two-stage-decision.png)

### Why the split

`decide` reads share prices and forms an opinion. `express` converts that
opinion into a specific contract to buy. Two functions, not one.

The reasoning is the same as the one that separated risk from strategy in
section 1. They are different jobs with different failure modes: a wrong
opinion loses money slowly and visibly, a badly chosen contract loses money
even when the opinion was right. Splitting them means each can be tested on its
own, and a bad contract choice cannot be mistaken for a bad strategy.

It also keeps what already exists. The year of SPY share prices already
downloaded is still the input to `decide`, and the look-ahead guard, the
recorded reasoning and the swappable-strategy promise all survive untouched.

### What the split does, and does not, fix about the delay

**Revised 2026-08-28**, because the first version of this passage was too
comfortable and a review caught it.

What it genuinely fixes: the system never has to **read** option prices in order
to form an opinion. Direction comes from the shares. The chain - the list of
available contracts - is consulted only to pick one, and strikes and expiry
dates do not change minute to minute, so a stale catalogue is still a perfectly
good catalogue.

**Measured 2026-08-28, market open — and the answer was better than either
draft assumed.** The fifteen-minute wall applies to what has *traded*. It does
not apply to what is currently *on offer*: the bid and ask come back stamped to
the current second. Three samples twelve seconds apart are in `options_data.md`.

So at the moment of ordering we can see the real current price of the contract.
The spread check works on live numbers, the order can be priced off a live
mid-price, and the rule barring entries before 09:45 — written when we believed
otherwise — is withdrawn.

**The cost lands somewhere else instead, and it is worse.** There is no
historical bid and ask *at all* — the endpoint does not exist, on any tier. So
the backtest cannot see the one number the live system will have. That inverts
the usual danger: **the live system will trade on better information than the
rehearsal that validated it.** Section 3 owns what to do about it.

### What `express` has to choose

Five decisions, and only the first is obvious.

| Choice | How it is made | Why |
| --- | --- | --- |
| Call or put | Straight from the view's direction | No judgement involved |
| Expiry | A swept parameter: days until the contract dies | A contract expiring today is the cheapest to trade and loses value fastest. One expiring next week holds its value longer but costs more to get into. There is no obvious right answer, so we measure it |
| Strike | A swept parameter: dollars away from the current share price | Yesterday's chain had a 0.3% cost at one strike and 3.9% two strikes away. Choosing by intuition is guessing |
| How many contracts | Sized on the money at risk, not the exposure | One at-the-money call cost $286 and controlled $77,000 of shares. The size is set from the $286, because all of it can go to zero |
| Whether to trade at all | **A hard gate: decline if the spread is wider than a threshold** | Some contracts cost several percent simply to enter and exit. The trade has to clear that before it clears anything else |

The two swept parameters are deliberate. The standing rule in this repository
is that thresholds are swept across a range and a human reads the resulting
table — never chosen because one value made a backtest look good. Expiry and
strike are exactly the numbers that get quietly tuned until the curve is
pretty, so they live in configuration from the first day.

The fifth row is new, and it is the most important. Until now every strategy in
this project could assume that trading was roughly free. On options it is not,
and a rule that is right slightly more often than it is wrong can still lose
money purely on the cost of getting in and out.

### Two rules fixed now, because this is where options bite

**Stops live on the shares, not on the option.**

An option's price jumps. It can leap straight past a stop level while the
shares themselves barely move, which turns a stop into a random exit. So the
trigger is written on the underlying — "get out if SPY crosses $768" — and when
that fires, the contract is sold at whatever it happens to be worth. The
decision stays in the clean, heavily traded instrument; only the execution
happens in the messy one.

**The consequence, which the first draft of this section missed:** a trigger
written on the shares is not an order sitting at the broker. Nobody is watching
it but us. Section 4 owns that loop, and owns what happens when our process is
not there to run it.

**A hard time-based exit, enforced by the risk layer.**

An option that reaches its expiry does not simply stop existing quietly. It
either gets **exercised** — meaning the account suddenly owns 100 actual shares
per contract, which at $770 a share is $77,000 that was never budgeted for — or
it expires worthless. Neither should be something the agent discovers by
accident.

So every position is closed by a fixed time each day, without exception. This
sits in the risk layer rather than the strategy, for the reason given in
section 1: a guarantee is only worth something if no strategy can override it.

### What this changes below

- **Section 3.** The cost model becomes per-contract rather than one flat
  number, because the cost varies by several multiples between neighbouring
  strikes.
- **Section 4.** The risk limits are rewritten around money at risk, since an
  option position can lose its entire value rather than a few percent.

Both were already flagged in `requirements.md` as needing rework.

---

## Section 3 — The backtest driver, and the checks that keep it honest

The backtest driver feeds the strategy a year of stored prices one minute at a
time, pretends to fill the orders it asks for, and writes down what happened.

Its job is not to produce a good number. Its job is to produce a number we are
allowed to believe.

### Where a backtest invents money

Look-ahead, handled in section 2, is the first way. Pretending an order filled
is the second, and it has two distinct traps.

**Trap one: filling at a price that was already history.**

The rules look at a completed minute and say "buy". But that minute is over —
its closing price is known only once the minute has finished. A backtest that
fills you at that closing price has handed you a price you could never have
obtained.

The rule: decide on the bar that just closed, fill at the opening price of the
next one.

![Five price bars, with the decision at the close of one bar and the fill at the open of the next](diagrams/fill-timing.png)

The gap between those two points is real money. The price can move between the
decision and the fill, sometimes against us. A backtest that fills at the
decision price silently deletes that risk.

**Trap two: an ambiguity with no correct answer.**

Every trade goes out with a **stop** — get me out if this goes wrong — and a
**target** — take the profit if it goes right.

Now consider a minute in which the price dipped low enough to hit the stop *and*
rose high enough to hit the target. A one-minute bar records four numbers: open,
high, low and close. It does not record the order in which they happened. Which
one filled first is genuinely unknowable from our data.

An assumption has to be chosen, and the choice is worth real money:

| Assumption | Effect |
| --- | --- |
| Target filled first | Optimistic. Manufactures winners out of ambiguity. This is how a great many hobby backtests produce beautiful curves. |
| Stop filled first | Pessimistic. Turns every ambiguous bar into a loss. |

**We assume the stop filled first.** Where a measurement is ambiguous, the
assumption that flatters us is the one most likely to be wrong in live trading.
A strategy that survives the pessimistic assumption is real; one that only works
under the optimistic assumption has told us something important, cheaply.

The driver also records **how often the ambiguity occurred**. If it affects 3%
of trades the assumption barely matters. If it affects 40%, the entire result
rests on a coin flip, and the report has to say so in its first paragraph.

### What trading costs

Alpaca charges no commission, so cost is two things:

- The **spread** — the gap between the price you can buy at and the price you
  can sell at. You pay roughly half of it entering and half exiting.
- **Slippage** — the price moving between the decision and the fill.

On shares this is nearly free: SPY's spread is about 0.001% of the money
involved. **On options it is the dominant fact.** Measured on 27 August, a
same-day SPY contract cost between 0.3% and 3.9% of the premium just to get in
and out - and the 3.9% one was two strikes away from the 0.3% one. A flat cost
number would be a fiction.

**The probe came back negative, and it changes the plan.** We can read every
price at which a contract actually traded, minute by minute, back to January
2024. We cannot read what its bid and ask were at any past moment — Alpaca
publishes no such history, and the endpoint returns 404 rather than a
permissions error. The spread is the largest cost in options trading and it is
the one number the past does not record.

So the cost model cannot measure the spread. It has to **estimate** it, and be
honest that it is estimating.

**What the options cost model has to contain:**

1. **A spread model, not a spread measurement.** Two ingredients, both of which
   we do have. First, the scatter of traded prices within a single minute:
   trades alternate between buyers paying the ask and sellers hitting the bid,
   so the width of that scatter carries the spread inside it. Second, live
   quotes **recorded by us**, starting today — every minute we are connected, we
   write down the real bid and ask. By Monday that is a calibration sample no
   backtest could otherwise have.
2. **The model expressed in terms that generalise**: spread as a function of how
   far the strike is from the share price, how long until the contract expires,
   and how violently the underlying has been moving. Not a lookup table for the
   contracts we happened to record.
3. **Fills that match section 4's live rules.** Entries fill only if the next
   minute's prices reached our limit; exits fill at what a seller would actually
   have received. Filling at the option's last traded price is the single most
   common way an options backtest lies to its author.
4. **The spread check simulated, and flagged as simulated.** Live, it reads a
   real quote. In the rehearsal it reads the model. That is a genuine break in
   the one-engine promise, it is confined to this one gate, and the report says
   so in its first paragraph rather than its appendix.
5. **The ambiguous-bar rule restated for two instruments.** The trigger is on
   the shares and the money is in the option, so a minute can be ambiguous in
   the share bar and priced in the option bar. The pessimistic assumption still
   wins, and the frequency still gets counted and reported.
6. **Both share feeds**, per section 4 — every candidate measured on each.
7. **The three-costs report**, now per contract: zero, the model's estimate, and
   double it. This was a formality when costs were a rounding error on shares.
   With an estimated spread on options it is the main defence: **if the answer
   flips between the estimate and double it, we do not have a result, we have a
   preference.**

**And a validation the competition itself provides.** During the four live
sessions we will have, for the first time, real quotes and real fills side by
side. Comparing the model's prediction against what we actually paid is a
genuine test of the cost model, and it belongs on the dashboard whether it
flatters us or not.

### What a run produces

Three files.

| File | Contents |
| --- | --- |
| Per-bar record | Every bar the strategy examined, with the rule's input values as they stood at that moment. Roughly 98,000 rows for one symbol-year, about 10MB. |
| Trades | Every trade: entry, exit, reason, cost, and whether its exit was ambiguous. |
| Summary | The headline numbers, the settings used, and a hash of the inputs. |

The per-bar file is what makes independent checking possible. Without it we are
asking people to trust the summary, and a result nobody can verify does not
exist.

### The checks

- **The look-ahead guard** from section 2, enforced here by the driver.
- **A random-entry control.** The identical machinery — same sizes, same costs,
  same stops — but entering at random times. If the strategy cannot beat
  coin-flip entries, there is no edge and the profit was market drift wearing a
  costume. This is cheap to build and it is the check that most often kills a
  strategy.
- **Buy and hold, as context.** What if we had bought in January and done
  nothing? Sometimes that is the honest winner.
- **Reproducibility.** The same data and the same settings must produce a
  byte-identical output, with a hash recorded in the summary.

And one precondition that is not a check: **if the run produces too few trades,
no conclusion is available regardless of the profit.** Thirty trades cannot
distinguish skill from luck. The floor pre-registered in
`hackathon/strategy_candidates.md` is 150. Below it, the honest report is
"underpowered" — not a profit figure.

### Deliberately not done: the independent audit

The wider repository has a standing rule that whoever writes the trading rules
must not also write the audit that re-derives them from the recorded data.
One author writing both means a misread specification passes its own inspection.

For this project that rule is **deliberately suspended**, because there is one
author and one week. It is recorded here rather than quietly dropped, and it is
a real reduction in confidence: the per-bar file still lets somebody else check
the work, but nobody has.

---

## Section 4 — The live trader

The backtest moves through time by adding one to a number. The live trader has
to survive a real day. **Five** things are true live that are not true in a
rehearsal, and each needs an answer.

![One minute in the life of the live trader: a bar closes, decide, express, the risk gate, the order, the journal row](diagrams/minute-loop.png)

Nothing in that loop is clever, and that is deliberate. `decide` and `express`
are the **same code the backtest called**. That is the entire point of the
one-engine-two-drivers decision: the thing that traded is provably the thing
that was tested.

### Difference 1 — time is real

The backtest never waits. The live trader wakes at the top of each minute,
fetches the bar that just closed, and has to be finished before the next one
arrives. Late is the same as wrong: a decision made on a stale picture is a
decision made on the wrong picture.

**The bar is not there at second zero.** Prices are still being reported when
the minute ends, so asking instantly returns nothing or returns a half-formed
bar. The policy: ask at second five, retry twice, and if the bar has not arrived
by second twenty, **skip the minute and journal the skip** with the reason.

A skipped minute is a recorded fact, not a silent hole. If skips turn out to be
common, that is something the write-up has to say, because a strategy that only
trades on the minutes our infrastructure kept up with is not the strategy that
was backtested.

### Difference 2 — orders fail

In a backtest every order fills. Live, an order can be refused, filled only
partly, or accepted *after* the network already gave up waiting and we assumed
it had not been.

That last case is the dangerous one. We send an order, hear nothing, try again,
and buy the position twice.

The defence is that **every order carries an identifier we construct ourselves**
— from the symbol, the timestamp of the bar that triggered it, the strategy
name, and **a counter**, because a single bar can legitimately produce more than
one order (an entry that gets shrunk and resubmitted, an exit retried at a wider
price). Without the counter, the second legitimate order collides with the first
and is silently refused. Alpaca rejects a duplicate identifier, so a retry can
never double the position.

**Corrected 2026-08-28.** An earlier draft claimed this meant "there is never a
case where we have to guess". That was too strong. A rejection tells us the
identifier was used; it does not by itself tell us what became of the order that
used it. So the rule is: **on any timeout, ask the broker for the order by our
own identifier before doing anything else.** The identifier makes the question
answerable; asking is still our job.

### Difference 3 — the program can die while holding a position

A laptop sleeps. A server reboots. A bug crashes the process. On restart,
whatever the trader *believes* it is holding is worthless information.

So on every start it asks Alpaca what the account actually holds, and works
from that answer. **The broker is the only source of truth.** This is cheap to
build, and it is the difference between a restart being a non-event and a
restart being an unmanaged position.

### Difference 4 — nothing closes itself any more

This is the correction that matters most in this section.

Section 2A put the stop on the **shares** — "get out if SPY crosses $768" —
because an option's price jumps, which makes a stop written on the option fire
more or less at random. Good reasoning, with a consequence the earlier draft did
not follow through: **once the trigger lives on the shares, no order sitting at
the broker is watching it.** There is no automatic exit. We have to run one.

So the minute loop has a second half, and it runs *before* the strategy is
consulted:

| Order | Step |
| --- | --- |
| 1 | Fetch the closed share bar |
| 2 | **If we hold anything, check the exits** — stop crossed, target crossed, or the clock has reached the flat-by time |
| 3 | If any exit triggered, sell the contract and journal it. Do not consider a new entry in the same minute |
| 4 | Only if flat: consult `decide`, then `express`, then the risk layer |

Both exit triggers are written on the **share** price and both are checked on
the same one-minute bars the strategy sees, so the backtest can reproduce them
exactly. The profit target is back: the earlier draft mentioned a target in
section 2, then quietly lost it when the stop moved onto the shares. A stop
without a target is not a strategy, it is a slow bleed.

**Partial fills.** An exit is repeated until the broker reports the position is
flat — never assumed complete. An entry that is only partly filled by the end of
its minute has the remainder cancelled, and we keep whatever filled. The
position we manage is always the one the broker says we have.

### Difference 5 — the exit depends on our process being alive

A stop written on SPY and enforced by our own loop is only as reliable as the
loop. If the process is dead at 15:45, the flat-by rule does not fire, a
same-day contract runs to expiry, and the account can wake up owning $77,000 of
shares nobody budgeted for.

**The earlier answer here was wrong and is withdrawn.** It proposed a far-away
stop placed at the broker on the option itself, as a dead man's switch. That
does not work. A stop on an option we *bought* fires when the option becomes
nearly worthless — a loss already capped at what we paid for it, and capped
again by the sizing rule below. It cannot fire when the option ends up
*valuable*, which is exactly the case that causes the exercise. It guarded the
harmless failure and left the expensive one alone.

**What replaces it: an independent scheduled flattener.** A separate, very small
job, on a different machine from the trader, that runs at a fixed time every
session and unconditionally tells Alpaca to cancel every open order and close
every position. It does not ask whether the trader is healthy. It does not read
prices. It has no opinions and no strategy — about twenty lines, and the less it
knows the better.

That covers both failures the far-away stop could not: our process being dead,
*and* the contract finishing in the money. It is also insurance we can test on a
quiet afternoon rather than discovering under pressure.

The trader flattens at its own time; the flattener runs a few minutes later, so
in the normal case it finds nothing to do and says so.

**Probe 3 ran on 2026-08-28 and the call works on an option position** — but it
returned one detail that changes how the flattener has to be written. The
response came back `status: 200` with the sell order marked `pending_new` and
`filled_qty: 0`. **A success here means the order was accepted, not that the
position is closed.** On this occasion it filled 37 milliseconds later, on a
contract nobody wanted, in a quiet market. Neither of those conditions is
guaranteed at 15:50 on a day when something has gone wrong, which is the only
day the flattener matters.

So the twenty lines get a few more: **call, then verify, then call again.** Ask
for the positions back; if the list is not empty, wait a couple of seconds and
repeat, for a small fixed number of attempts. Then, whatever happened, write
down what it found and what it did — a flattener that fails silently is worse
than none, because it converts a loud problem into a quiet one.

One detail worth keeping: Alpaca stamped the closing order `position_intent:
sell_to_close` by itself. We never have to ask it to distinguish selling
something we own from selling something we do not — which matters, because
selling an option we do not own is the one thing the risk rules forbid
outright.

### How an order actually gets priced

The review of this design found the sharpest hole here, so it is answered
explicitly rather than left to implementation.

**Revised 2026-08-28 after measuring it.** An earlier version of this section
assumed we would be pricing orders off a fifteen-minute-old option price. We are
not: bid and ask come back live, to the second. What follows is built on the
measurement rather than the assumption.

Two different rules, because entering and exiting are not the same problem.

**Entering — a limit order, and we would rather miss than overpay.** A **limit
order** names the most we will pay. We take the current mid-price — halfway
between the bid and the ask — add a small stated allowance for movement between
sending and filling, and send that. If it does not fill within the minute it is
cancelled and the trade is simply not taken. The allowance is a swept parameter
like any other, and **missed entries are counted and reported**: a strategy that
only works when we chase the price is a strategy we do not have.

**Exiting — a market order, because not getting out is worse.** A **market
order** takes whatever price is available. Its downside is a bad fill; the
alternative downside is holding a same-day option past its expiry, which is the
$77,000 problem. That asymmetry settles it.

**The spread check runs on the live quote.** Measured this morning, the same
contract went from a one-cent spread to a five-cent spread and back inside
twenty-six seconds. On a $1.63 contract, five cents is 3% of the money at risk,
paid twice. Checking that against a stale number would have been close to
useless; checking it live is the single most valuable thing the free tier gives
us.

**The backtest must mirror the fill rules exactly**, or the parity claim this
document rests on is false. Entries fill in the rehearsal only if the next
minute's prices actually reached our limit; exits fill at the price a seller
would have received, never at the option's last traded price.

**And here is the honest problem that remains.** The backtest has no historical
bid and ask, because Alpaca does not publish any. So the live trader will be
making a decision — the spread check — that the rehearsal could only ever
*model*. The parity promise holds for `decide` and `express`, which see the same
share prices in both worlds. It does **not** hold for the spread check, and
pretending otherwise would be exactly the kind of quiet lie this design exists
to prevent.

What that means in practice is in section 3: the spread is modelled from what we
can see, the model is calibrated against live quotes, and every result is
reported at zero cost, at the model's estimate, and at double it. The write-up
has to state the limitation in its first paragraph rather than bury it.

### The two feeds, and a parity problem inherited from the shares work

Alpaca's free tier gives us two different sources of share prices, and we are
forced to use a different one in each half of the project:

- **SIP** merges the trades from every US exchange. Complete, but we may only
  see it after a fifteen-minute delay — so it is the **backtest** feed.
- **IEX** is a single small exchange carrying roughly 2% of all trading. It is
  real-time and free — so it is the **live** feed.

`strategy_candidates.md` already made this binding and this design failed to
carry it across: **anything validated on SIP and deployed on IEX is untested at
the moment it places real orders.** Worse, any rule that mentions trading volume
changes meaning between them. "Volume is 2.1 times normal" describes the whole
market on SIP and one small exchange having a busy minute on IEX. Those are not
the same sentence, and that exact phrasing is the example used in section 2.

**The rule, restated here so it cannot be missed: every candidate is backtested
twice, once on each feed, and is only eligible to trade live if it survives
both.** A rule that passes on SIP and fails on IEX is a rule we cannot run,
regardless of how good the SIP number looks.

### Staying alive, and knowing when we are not

Automatic restart handles a crash. It does not handle a process that is running
but stuck, or a host that has quietly gone away — and with four trading sessions
in total, a session lost to a hang nobody noticed is a quarter of the entire
record.

So the trader writes a **heartbeat**: a timestamp updated every minute it
completes a loop. A tiny external checker looks at that timestamp and raises an
alarm on Sami's phone if it stops advancing during market hours. This is an
hour of work and it is the difference between losing ten minutes and losing a
day.

### When the journal is unreachable

Supabase can be down or unreachable while the market is open. The policy, chosen
now rather than in the moment:

**Trading never blocks on the journal, and no row is ever lost.** If a write
fails, the row goes to a local file and is flushed when the connection returns.
The alternative — halting trading because a database is unavailable — trades a
recoverable inconvenience for an unrecoverable gap in the trading record.

The one exception is at startup: if the journal cannot be reached *and* the
local buffer is unreadable, the trader does not start. Beginning a session with
no ability to record what it did is the one case where not trading is correct.

### The risk limits, rewritten for options

Section 1 promised a risk file short enough for a judge to read in twenty
seconds and confirm the system cannot bet the account. This is what it holds.

The rewrite was necessary because a share loses a few percent on a bad day and
an option can lose **all** of its value.

**The numbers below are proposals and need Sami's explicit approval before any
of this runs.** An earlier draft of this table described each limit without
naming a single figure, which asked for approval of something that could not be
evaluated. The account is $100,000.

| Limit | The number | Why this one |
| --- | --- | --- |
| Per trade | **$1,000 of premium** — 1% of the account | The money paid for the contract is the whole loss. Size on what can vanish, not on the $77,000 of shares it controls |
| Open positions | **2 at once**, so at most $2,000 at risk | Four sessions is not enough time to spread risk properly. It is plenty of time for two positions to lose together |
| Daily loss | **Stop for the day at −$2,000** — 2% of the account, counting open positions at their current value | Stops one bad session from ending the competition |
| End of day | **Flat by 15:45 New York time**, no exceptions, with the independent flattener sweeping at 15:50 | The expiry trap described in section 2A |
| Direction | **Buy contracts only.** Never sell one we do not own | Our approval level blocks it anyway, but asserting it in our own code means the guarantee does not depend on a setting in someone else's dashboard |

**Drawdown**, used above, means a fall from a high point — here, how far the
account is down from where it started the day.

If the reduced-size fallback in section 7 is triggered, every money figure above
is divided by four and the position limit drops to one.

The risk layer can only ever say **no** or **smaller**. It has no path that
increases anything.

**One tension worth naming.** Profit and loss is judged, and a hard daily stop
can prevent a recovery that would have happened. The limit stays anyway: across
four sessions, blowing up on day two scores zero, and a system that demonstrably
cannot ruin itself is the stronger submission. But this is a trade-off, not a
free win, and it is recorded as one.

---


## Section 5 — The journal

Three tables in Supabase. The trader only ever writes to them. The dashboard
only ever reads them. Nothing else touches them at all.

![The live trader writes three tables in Supabase; the dashboard only reads; the backtest writes files instead](diagrams/journal.png)

### Table 1 — `decisions`

One row for **every minute the strategy was consulted**, not only the minutes it
acted. About 390 rows a session - call it 1,600 to 2,000 across the four
counted sessions plus Monday's dry run. That is nothing for a database.

Recording the quiet minutes is the entire point. It lets the dashboard say "we
looked 1,900 times and acted 11 times", which is a far more honest picture of a
trading system than a list of its trades. And it means anybody can recompute
the rule on every bar, rather than only on the bars we happened to like — which
is what makes the repository's standing rule enforceable: a result nobody can
independently verify from recorded evidence does not exist.

| Column | Holds |
| --- | --- |
| `decided_at` | The timestamp of the bar, in UTC, always |
| `strategy`, `version` | Which rules, and which revision of them |
| `params_hash` | A fingerprint of the exact configuration in use |
| `bar` | The open, high, low, close and volume the decision actually saw |
| `evidence` | The rule's input values at that instant. Stored as JSON, because they differ from one strategy to the next |
| `view` | Up, down, or nothing |
| `reason` | One sentence of plain English |
| `expression` | The contract chosen, or the reason it declined to choose one |
| `risk_verdict` | Allowed, shrunk, or refused — and why |

### Table 2 — `orders`

One row for every order actually sent to the broker.

| Column | Holds |
| --- | --- |
| `client_order_id` | The identifier we built ourselves, from section 4. This is the thing that makes a retry safe |
| `decision_id` | Which decision caused this order |
| `contract`, `side`, `quantity` | What was asked for |
| `submitted_at`, `status`, `filled_at`, `filled_price`, `filled_quantity` | What happened to it |
| `broker_order_id` | Alpaca's own reference, for cross-checking |
| `error` | The rejection message, where there is one |

### Table 3 — `sessions`

One row per trading day: starting and closing equity, realised profit and loss,
decisions made, orders sent, fills received, and whether either safety rule
fired — the daily loss limit, and flat-by-a-fixed-time.

Those last two are deliberate. If a safety rule fires, the dashboard says so
out loud. A system that reached its loss limit and stopped trading is a system
doing its job, and quietly omitting that would be the dishonest choice.

### Three rules about the journal

**`decisions` is append-only.** A decision is a historical fact: it is never
updated and never deleted. `orders` is the one exception, because the broker
owns an order's status and we are only mirroring it. That is also why section 4
re-syncs from Alpaca on every restart instead of trusting this table.

**Every timestamp is stored in UTC** and converted to New York time only at the
moment it is displayed. Mixing the two is one of the most reliable ways to
produce a trading bug that only shows up twice a year, when the clocks change.

**The backtest writes files, not Supabase.** Ninety-eight thousand rows per run
would swamp a journal built for two thousand, and mixing the rehearsal with the
real record is precisely the confusion this whole design exists to prevent. The
columns match, though, so the same dashboard code can render either one.

And one thing that never appears in any column, ever: an API key.

---

## Section 6 — The dashboard and the ask box

### What the page shows

Four things, in order of how much they actually prove.

1. **The rehearsal against the reality.** What the backtest predicted, beside
   what really happened. This is the visible proof of the one-engine-two-drivers
   decision, and few other submissions will be able to show it.

   **What the comparison actually is**, because "predicted" is doing too much
   work otherwise: the backtest ran over a year, so it produces a *spread* of
   possible four-day outcomes, not a single forecast. The panel shows where our
   four real sessions landed inside that spread. And it carries a fixed
   sentence, written before the sessions happened: **four sessions can show the
   live system behaving consistently or inconsistently with the rehearsal. It
   cannot confirm that the strategy works.** That sentence stays on the page
   whatever the result.
2. **The decision feed.** Every minute the system looked, what it concluded,
   and the numbers behind that conclusion - including the roughly 1,900 minutes
   it decided to do nothing.
3. **The trades**, each with its reason and its cost.
4. **The safety rules**, and whether any of them fired.

### Where the AI actually sits

The competition's headline requirement is an **autonomous AI agent**, and
`requirements.md` left this as an open question. It is answered here, and the
answer is deliberate rather than convenient.

**The trading rule is not an AI.** It is a small, fixed, deterministic rule -
given the same prices it always does the same thing. That is not a compromise,
it is the only reason any of the evidence in this document means anything: a
rule that answers differently each time it is asked cannot be backtested, cannot
be reproduced byte for byte, and cannot be checked by anyone else. Handing
minute-by-minute trading decisions to a language model would have made the
system less trustworthy and the write-up unfalsifiable.

**The AI sits in the explanation layer — the ask box below.** A model reads the
system's own recorded decisions and answers questions about them in plain
English, for anybody, without needing to be taught what the columns mean.

**Autonomous** describes the operation, and that part is literal: for four
trading sessions nobody touches it. It wakes itself, decides, sizes, orders,
manages its own exits, stops itself at its loss limit, closes its own positions
before expiry, restarts itself after a crash, and writes down everything it did.
No human is in the loop at any point during a session.

So the honest sentence, and the one the write-up should use: **the trading is
autonomous and auditable; the AI makes it explicable.** A submission that put a
language model in charge of the orders would look more like the brief and be
worth considerably less.

### The ask box

A public text box. Anyone can ask about the system in plain English.

This is where the submission becomes interesting and where it becomes
dangerous, so the security *is* the design rather than a layer applied on top
of it.

![Two panels: what the public page can reach, and what it cannot](diagrams/ask-box-boundary.png)

**The central decision: the model never writes a query.**

It is given a small fixed menu of read-only operations that we wrote — fetch a
session, fetch the trades in a date range, fetch the decisions around a
timestamp — with every parameter checked before it runs. The model chooses
which one to call and with what dates. It never composes a database query
itself.

That is the difference between asking a system nicely not to do something, and
the capability not existing.

| Threat | Why it fails here |
| --- | --- |
| **Prompt injection** — a visitor types "ignore your instructions and place an order" | There is no code path from the ask box to Alpaca. It is not blocked, it is absent. The box's entire world is journal rows |
| **Key extraction** — "print your environment" | The Anthropic key is held by the server function in order to make the call. It is never text the model can see. No Alpaca key exists in that process at all |
| **Database injection** | The model does not write queries. See above |
| **Cost abuse** — someone loops the box and burns through the billing account | A rate limit per visitor, a hard daily ceiling on total calls, and short maximum answers. A public box with a billing key behind it is otherwise a bill waiting to happen |
| **Credential harvesting** | There is no field anywhere on this page that accepts an API key. That is the exact shape of the attack, so we do not build that shape |

**Two further rules, about honesty rather than attackers.**

The box answers *only* from the rows it was handed, says plainly when something
is not in the record, and **shows which rows it used**. A visitor can check the
answer against the evidence. That is the principle this whole repository runs
on, applied to the presentation layer.

And it declines to give financial advice. "Should I buy SPY?" gets a straight
no. It describes what this system did. It does not tell anybody what to do with
their money.

### The limitation, stated plainly

A fixed menu means the box cannot answer questions nobody anticipated. "Which
weekday was best?" is out of reach unless we wrote that query.

That is a real cost and it is being chosen deliberately. A box that can reach
anything in a database is a box that someone will eventually persuade to reach
something we did not intend. When a question turns out to matter, it gets added
to the menu — twenty minutes of work, and it stays safe.

### Two smaller things

**Database keys.** The database itself enforces read-only for anything the
public can touch. The write-capable key exists only on the trader's machine and
never reaches the dashboard host, let alone a browser.

**The page must survive an empty database.** The opening section of this
document flagged that Supabase is believed to pause inactive free projects,
which would matter if a judge opened the page a fortnight after submission. So
the final journal is also committed to the repository as a static file, and the
page falls back to it. Cheap insurance against the demonstration being blank at
exactly the wrong moment.

---

## Section 7 — Build order and the calendar

![Eight days from Friday 28 August to Friday 4 September, with four counted trading sessions](diagrams/calendar.png)

### What depends on what

Only one chain is genuinely sequential: **strategy, then backtest, then the
decision gate, then the live trader.** Everything else runs beside it.

The dashboard is the case that matters. It depends on the journal's *columns*,
which section 5 fixed, and not on any result. So it is built against invented
journal rows and finished before there is anything real to put in it. Had it
been made to wait for the backtest, a backtest that took two days would have
cost us the dashboard as well.

One new piece of work the calendar has to absorb. A year of SPY **share**
prices is already downloaded; option prices are not. That downloader is
Saturday's first job. It is a variant of the one already written, and the
probes recorded in `options_data.md` confirmed the data is there to fetch.

### Three hard deadlines

| When | What | What it costs to miss |
| --- | --- | --- |
| **Friday 28 August** | The brand-new paper account exists, funded at $100,000 | Everything done before it is prototyping. None of it counts |
| **Tuesday 1 September, 09:30 New York time** | The trader is running live on that account | Every session lost is a quarter of the entire trading record |
| **Friday 4 September** | Submitted | — |

The middle deadline is the real constraint. Four sessions is already thin.
Three would be very thin.

**Friday is both a trading session and the submission day**, and that is a
decision, not an oversight. The market is open until 16:00 New York time and
that session counts, so the trader runs. What it must not do is hold the
submission hostage: **the video, the slides and the write-up are finished on
Thursday night from three sessions of evidence**, and Friday's session flows
into the live dashboard on its own. Nothing that has to be edited by hand waits
for Friday's closing bell.

### Where the trader runs

**A small always-on virtual server**, roughly five dollars a month, with the
process supervised so that it restarts by itself. That pairs directly with the
section 4 design: a restart is a non-event, because the trader reconciles from
the broker rather than from its own memory.

Not a laptop. It sleeps, and it would have to stay awake from 09:30 to 16:00
New York time for four consecutive days.

Not the free tiers of the easier deployment platforms. They are simpler to
deploy to, but they suspend idle processes in ways we would have to learn under
deadline pressure. A plain virtual machine has fewer surprises, and fewest
surprises is precisely what is being bought here.

### What gets cut, and in what order

Every schedule slips. Deciding the cut order now, while nothing is at stake,
avoids cutting the wrong thing at midnight on Wednesday.

Load-bearing, never cut: **the trader running live on the new account**, the
journal, the backtest with its costs, and the write-up. Without any one of them
there is no submission.

The cut order, first to go:

1. **The ask box degrades to a fixed page of questions and answers** we write
   ourselves. It is the most novel piece and it is not on the required-artifacts
   list, which makes it the correct first sacrifice.
2. **The dashboard's rehearsal-against-reality panel goes**, leaving the
   decision feed, the trades and the safety rules. Those three still show a
   working, honest system.
3. **The sweep shrinks** to a single expiry and a single strike rule, chosen by
   the selection rule below rather than by looking at outcomes.

Nothing on that list touches the trading or the record of it.

### How the winning settings get chosen

Section 2A sweeps two parameters, expiry and strike, on top of the strategy's
own thresholds. That is dozens of combinations, and a table of dozens of results
with a human choosing the prettiest one is how this repository has produced
wrong answers before.

**So the rule is fixed here, before the table exists:** we take the setting
nearest the middle of the largest *contiguous block* of settings that clears
costs — never the single best cell. A lone good cell surrounded by bad ones is
noise. A broad region that works is the only kind of result that survives
contact with a different year.

If no contiguous block clears costs, the reduced-size fallback applies and the
write-up says so.

### The fallback, pre-committed

If the backtest says the strategy does not clear its costs, we will know on
Sunday night. **What happens then is decided now, before the answer is known.**

The temptation will be to spend Monday inventing a replacement. That is the
worst option available. A rule invented under deadline pressure has been tested
against nothing, and it would be carrying the entire trading record.

**So: we run the original candidate anyway, at reduced size, and report both
results.** We tested it, it did not clear the bar, we ran it small, and here is
what happened.

That keeps a real profit-and-loss record, keeps the system genuinely
autonomous, and makes the write-up stronger rather than weaker — it becomes the
only submission that shows its own negative result and ships regardless.

Committing to this before knowing whether we will like the answer is the entire
point of writing it down here. A fallback chosen after seeing the result is not
a fallback, it is a rationalisation.

---

## Section 8 — What we have assumed but not measured

This section exists because a cold review of this document found that its most
confident passages were the ones resting on unmeasured assumptions, while the
passages built on `options_data.md` — where every claim was probed and the
commands published — were the sound ones.

The difference was not care. It was that one set had been checked and the other
had been reasoned about. So everything still unchecked is listed here, with what
breaks if the assumption is wrong, rather than left to be discovered on the day
it matters.

**Each probe was minutes of work.** None was research; they were questions with
an answer sitting behind an API call. Five have now been run, and one of them
overturned an assumption this design had been carrying since its first draft.

**Five of the seven were run on 2026-08-28 with the market open.** Results in
`options_data.md`; the consequences are already written into the sections above.

| # | Assumption | Result | What it changed |
| --- | --- | --- | --- |
| 7 | Option quotes are usable during market hours | **Better than assumed.** Bid and ask are real-time to the second; only trades and bars are delayed | The spread check and order pricing run on live numbers. The no-entries-before-09:45 rule is withdrawn |
| 1 | Alpaca serves historical bid and ask for expired contracts | **No. The endpoint does not exist** — 404, not a permissions error | Section 3 rebuilt around a spread *model* calibrated on quotes we record ourselves. The largest single change on this page |
| 6 | The greeks are permanently absent | **Confirmed absent**, market open, contract actively quoted | Nothing. We never depended on them |
| 5 | The account is approved to buy options | **Level 3, $100,000, active** — on the *existing* practice account | Nothing yet. **Still to be re-checked on the new competition account**, which is the version that matters |
| 4 | A year of history downloads in reasonable time | **Comfortable.** 20 sequential requests in 14.8s, no rate limiting; 30 contracts in a single 0.96s request | Saturday's download is minutes, not hours. Paging has to be handled |
| 2 | Alpaca accepts stop orders on options | **Not run** — requires submitting an order | Nothing depends on it since the flattener replaced broker-side stops |
| 3 | Close-all-positions works on option positions | **Yes**, run by Sami on a real option position. But a 200 means *accepted*, not *closed* — the order came back `pending_new` and filled 37ms later | The flattener now has to verify rather than assume: close, re-read the positions, retry, and journal the outcome |

**Probe 3 was run by Sami on 2026-08-28** — both it and probe 2 mean placing
orders, which is not something I do on his behalf. It passed, and it improved
the flattener: see Difference 5. Probe 2 is still unrun and still does not
matter, because nothing depends on broker-side stops any more.

**Probe 5 must be repeated on the new account** the hour it exists. A fresh
account approved at level 1 cannot buy a call, and everything else is wasted
until that is fixed.

**A methodological note worth more than any single result.** Two of this round's
failures were fake: a shell rewriting a URL, and a command talking to the wrong
host, each producing a 404 indistinguishable from the real one. The genuine 404
in probe 1 was only believable after the URL had been printed and a neighbouring
endpoint returned 200 from the same prefix. **A negative result is not a finding
until you have proved you asked the question correctly** — which is the same
lesson as the 0-of-52 retraction, arriving in a smaller costume.

**Everything measured gets written down the way `options_data.md` writes things
down**: the command, the raw answer, the date. A probe whose result lives only
in a chat window has not been recorded, and this repository has a standing rule
that a result nobody can independently verify does not exist.

---

## The design is complete

Sections 1 to 8 are settled. What follows is a separate document: the
implementation plan, which says which file to write first. Nothing in this
document says that, deliberately.

**Revised 2026-08-28 after an independent review.** The design was declared
complete earlier the same day and then read cold by a reviewer with no memory of
writing it. That review found one outright error - a safety device aimed at the
wrong failure - several unverified assumptions presented as settled, and a risk
table that asked for approval of limits it never named. All of it is fixed
above, and the corrections are marked in place rather than quietly folded in, so
the reasoning can be followed by anyone who reads this later.

Two things remain genuinely open:

- **Section 3's cost model still has to be built** — and it is now a harder job
  than it looked, because probe 1 came back negative and the spread has to be
  modelled rather than measured. What it must contain is written out in that
  section.
- **One probe remains and nothing depends on it**: whether Alpaca accepts a
  stop order on an option. The probe that mattered — whether the
  close-everything call works on an option position — was run on 2026-08-28 and
  passed, with one correction to how the flattener has to be written.
- **Probe 5 has to be repeated on the new competition account.** It passed on
  the practice account, which is not the account we will trade.

