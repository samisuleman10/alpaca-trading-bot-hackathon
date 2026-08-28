---
title: Intraday 1-minute candidates for the Alpaca hackathon
status: spec written, nothing measured
owner: Sami
first_proposed: 2026-08-27
target_repo: fresh public repo (NOT ai-trade — see auto-memory alpaca-hackathon)
---

# Intraday 1-minute candidates — specification

## Read this first

**What we are asking.** The hackathon organisers handed every entrant a ready-made
strategy: buy a stock when it drops half a percent below its average price for the
day, sell when it comes back. They provided no evidence that it works. We are
asking whether it does, before we spend a week building around it.

**What we are also asking.** Sami proposed a second idea — the Williams Alligator,
a trend indicator. This repository already tested that indicator and it failed
badly. But it failed on *different* bars, in *different* markets. Testing it again
here costs almost nothing once the machinery exists, so the data decides rather
than either of us.

**What we should do about it.** Nothing goes live until one of these two candidates
survives a backtest against a pass/fail rule committed in advance. If both fail,
that is the result — and it is a better presentation than a pretty equity curve
nobody checked.

**Vocabulary used below**, each explained once:

- **Bar** — one row of price history covering a fixed slice of time. A 1-minute bar
  records the first, highest, lowest and last price traded in that minute, plus how
  many shares changed hands.
- **VWAP** (volume-weighted average price) — the average price everyone has paid so
  far today, weighted by how much traded at each price. A stock below its VWAP is
  cheap relative to what the day's buyers have paid on average.
- **R** — profit measured in multiples of what was risked. Risk $100 to make $100
  and win, that is +1R. `−0.1033R` means each trade lost about a tenth of what it
  put at risk, on average.
- **t** — how many standard errors a result sits from zero. Rough reading: below
  about 2 is indistinguishable from luck; −8 is decisive, and bad.
- **SIP / IEX** — two sources of US stock prices. SIP merges every exchange. IEX is
  one small exchange carrying roughly 2% of the trading.

## Purpose

Decide, on recorded evidence, whether either candidate is worth deploying to an
Alpaca paper account for the 28 August – 4 September 2026 build week. This document
fixes the rules **before** any result is read. Nothing here is approved for
execution.

## The data constraint that shapes everything

Measured directly against the account on 2026-08-27, not read off a pricing page:

| Feed | Availability | Density (SPY, 2025-06-02 15:00Z) | Usable for |
| --- | --- | --- | --- |
| SIP | history only; 403 inside 15 minutes | 1,995 trades / 149,320 shares | backtest |
| IEX | real time, ~1 min old | 39 trades / 2,558 shares | live |

SIP 1-minute history reaches back to at least 2019-01-02; IEX to somewhere between
June 2019 and June 2021.

Two consequences, both binding:

1. **The backtest and the live run cannot use the same feed.** Anything validated
   on SIP and deployed on IEX is untested at the moment it places real orders. Same
   class of error as the look-ahead leak found in `external/`.
2. **Any rule keyed to trading volume changes meaning between the two.** On SIP,
   "volume is 1.2x its recent average" describes the market. On IEX it describes one
   small exchange having a busy minute. These are not the same statement.

Therefore **every candidate is measured twice**: once on SIP bars, once on IEX bars,
over the same period. A rule is eligible for live deployment only if it passes on
both. A rule that passes on SIP and fails on IEX is a rule we cannot run.

## Candidate A — VWAP mean reversion (the organisers' baseline)

Restated as exact machine-readable rules. Where the original prompt is silent, the
choice appears under *Assumptions* below — marked, not buried here.

```
universe        = [SPY, QQQ, IWM, AAPL, MSFT, NVDA, AMD, TSLA, META, AMZN]
bars            = 1-minute, regular hours only (09:30-16:00 America/New_York)

session_vwap[t] = sum(bar.vw * bar.v, bars 09:30..t) / sum(bar.v, bars 09:30..t)
price[t]        = close of bar t
dist[t]         = (price[t] - session_vwap[t]) / session_vwap[t]
avg_vol_20[t]   = mean(volume of bars t-20 .. t-1)        # EXCLUDES bar t
vol_ratio[t]    = volume[t] / avg_vol_20[t]

ENTRY (long only), evaluated on the close of completed bar t:
    dist[t]       <= -0.005
    vol_ratio[t]  >= 1.20
    no open position in symbol
    >= 10 minutes since last entry in this symbol
    open_positions < 4
    exposure + 100 <= 500
    t <= no_new_entry_cutoff
  -> market buy, $100 notional, filled at the OPEN of bar t+1

EXIT, evaluated on the close of completed bar t:
    dist[t] >= -0.001                                  # returned to VWAP
    OR minutes_held > 15
    OR (price[t] - fill_price) / fill_price <= -0.005
    OR t >= force_flat_time
  -> market sell, filled at the OPEN of bar t+1
```

### The arithmetic to beat, recorded in advance

Entering at exactly −0.5% and exiting at −0.1% earns about **0.40%**. The stop cuts
at **0.50%**. Risking 0.5 to make 0.4 requires being right **55.6% of the time
merely to break even**, before costs.

This is not grounds to reject the rule unmeasured. It is the number the measurement
has to clear, written down now so it cannot be moved later.

### Assumptions and missing inputs

The organisers' prompt does not specify these. Each is a decision, not a detail, and
each is swept or stress-tested rather than assumed correct.

1. **VWAP price input.** Alpaca's per-bar `vw` field weighted by volume, not
   `(high+low+close)/3`. Both defensible; the choice is recorded.
2. **Session boundary.** VWAP resets at 09:30 ET and excludes pre-market entirely.
   The prompt says "regular market hours only" but not whether VWAP accumulates from
   the pre-market open.
3. **Rolling volume window excludes the current bar.** Including it makes
   `vol_ratio` partly a function of itself, biasing it toward 1.
4. **Execution at the open of bar t+1**, never its close. Signal on t, act on t+1,
   no exceptions. This is the look-ahead guard.
5. **`no_new_entry_cutoff` = 15:45 ET, `force_flat_time` = 15:55 ET.** The original
   rules contain **no close-out at all**, which would leave positions held overnight.
   A 15-minute intraday rule carrying overnight risk is a different strategy. This is
   a defect in the source prompt, not a judgement call.
6. **Stop measured from fill price**, not signal price.
7. **The $500 exposure cap is dead code.** Four positions at $100 notional is $400,
   which can never reach $500. Recorded because an inert constraint that looks active
   is how a risk layer comes to be trusted while doing nothing.
8. **Cooldown restarts on entry**, not on exit.
9. **Fractional shares.** $100 of a $770 stock is 0.13 shares. Alpaca supports
   fractional quantities; whether they fill identically in paper must be verified,
   not assumed.
10. **Costs.** Alpaca charges no commission, but the spread is real, and Alpaca's own
    documentation states paper trading does not simulate slippage, market impact,
    queue position or regulatory fees. A cost model is applied in backtest and swept.
    An edge that dies under plausible costs is not an edge.
11. **Missing bars and halts.** A minute with no trades produces no bar. Indicators
    must handle gaps explicitly rather than silently shifting their windows.
12. **Long only.** The prompt never mentions shorting; we do not add it.

## Candidate B — Williams Alligator

Three smoothed moving averages of different lengths, each pushed forward in time —
jaw (13, shifted 8), teeth (8, shifted 5), lips (5, shifted 3). Tangled together
means no trend; fanned out in order means a trend is running.

**This indicator is already falsified in this repository.** Strategy 03, recorded
1 August 2026, described there as the most strongly evidenced result we hold:

| Scope | Trades | Avg R | t |
| --- | ---: | ---: | ---: |
| Pooled, 8 instruments, 15-minute bars | 5,602 | −0.1033 | **−8.23** |

Seven of the eight instruments fired the abandon rule independently, across US
equity indices, small caps, precious metals and spot FX.

The present test does differ — 1-minute bars rather than 15, single US stocks rather
than indices and FX. That is a real difference. It is also exactly the shape of
reasoning that manufactures false discoveries: re-testing a dead idea on new data
until some slice of it looks alive.

**Candidate B therefore carries a raised bar, fixed now.** It is adopted only on a
result that would survive being wrong twice: it must pass the same decision rule as
Candidate A, **and** pass on the sealed holdout, **and** show a consistent direction
on at least 7 of the 10 symbols. A pooled win driven by one or two symbols is not a
finding — it is the earlier result reappearing with extra steps.

## Sample split, fixed before any measurement

| Period | Role |
| --- | --- |
| 2021-07-01 → 2024-12-31 | development — thresholds swept here, read freely |
| 2025-01-01 → 2026-08-27 | **sealed holdout** — opened once, for one candidate |
| 2026-08-28 → 2026-09-04 | live paper, 6 sessions — genuine forward test |

The start date is 2021-07-01 so SIP and IEX cover the same span and the two-feed
comparison is like for like.

The live week is **6 trading sessions**: 28 and 31 August, 1–4 September. It is a
demonstration, not evidence, and must be labelled as such wherever it is shown.

## Power budget, pre-registered

Strategy 04 was closed partly because its samples could never have resolved the
effects it claimed. Fixing the requirement in advance:

- **Minimum 150 trades per symbol** in the development period for a configuration to
  be read at all.
- A configuration producing fewer is reported as **underpowered**, never as negative.
  "We could not tell" and "it does not work" are different findings.
- The smallest effect the sample could have resolved is reported beside every result.

## Decision rule, pre-registered

Scored with the committed rule in `scripts/evaluate_holdout_significance.py`,
**ported unchanged** to the hackathon repo. The arithmetic is not to be rewritten —
significance calculations living in a chat transcript are how the 0-of-52 retraction
happened.

Per symbol: **accept** / **abandon** / **neither**, plus the resolvable-effect floor.
Pooling across symbols uses a rule stated in advance, and specifically **not**
`max(member p)`, which vetoed 52 of 52 hypotheses and had to be retracted.

## Producer output required for auditing

Every decision value written at decision time to `candidate_signals.csv`, one row per
symbol per evaluated bar:

```
timestamp, symbol, feed, close, session_vwap, dist, volume, avg_vol_20,
vol_ratio, open_positions, exposure, minutes_since_last_entry,
proposal, risk_verdict, risk_reason, fill_price, fill_timestamp
```

Every reference timestamp must be **≤ the decision timestamp**. An audit module
re-derives each rule from these columns alone, importing nothing from the strategy
package, and **must be written by a different author from the rules module** — one
author writing both means a misread spec passes its own check.

## Required ablation

Measured per symbol, never pooled-only, on SIP and on IEX:

| Variant | Question it answers |
| --- | --- |
| Baseline as specified | does the organisers' rule work at all? |
| Volume filter removed | is `vol_ratio >= 1.2` doing anything? |
| Entry threshold 0.3 / 0.4 / 0.5 / 0.7 / 1.0 % | is 0.5% an artefact? |
| Time exit 5 / 10 / 15 / 30 / 60 min | is 15 minutes an artefact? |
| Stop 0.3 / 0.5 / 0.8 / 1.2 % | does the risk/reward asymmetry bind? |
| Costs 0 / 1 / 2 / 4 bp per side | where does the edge die? |
| Random-entry control | does the exit logic alone produce the P&L? |

The random-entry control is the positive-control lesson from the retraction: a test
that cannot reject a known-null input is not testing anything.

## Research warning

- **Every threshold in Candidate A arrived from the organisers with no stated
  provenance.** 0.5%, 0.1%, 1.2x, 20 minutes, 15 minutes, 4 positions — not one is
  supported by any evidence available to us. They are somebody else's in-sample
  parameters and must be swept before any is believed.
- **The ten symbols are not ten independent tests.** SPY, QQQ and IWM are baskets of
  companies; six of the seven single stocks are large constituents of QQQ. When the
  market falls below its average, they fall together. Our own eight instruments were
  worth roughly three independent tests; this universe is worse. Pooled significance
  that ignores this is overstated.
- **Candidate B is a re-test of a falsified result** and is treated as such.
- **Nothing here is approved for live trading**, and the account is paper-only
  regardless.

## Promotion criteria

A candidate may be deployed to the live paper account only when, for the exact
configuration proposed:

1. It passes the pre-registered decision rule on the development period, on **both**
   SIP and IEX bars.
2. It clears the 150-trade-per-symbol power floor.
3. Parameter sweeps show the result is not a threshold artefact.
4. The edge survives cost stress at 2 bp per side.
5. It then survives the sealed holdout, opened **once**.
6. Candidate B additionally shows directional consistency on ≥ 7 of 10 symbols.

If neither candidate clears these, the deliverable is the negative result plus the
harness that produced it. That is a legitimate submission, and an honest one.
