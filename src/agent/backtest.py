"""Replay the past one minute at a time, and write down what would have happened.

A **backtest** is a rehearsal against recorded history. Its one job is to
produce a number you can trust, and almost every way of writing one produces a
number you cannot. So the conventions matter more than the code, and each is
named here rather than left to be inferred from the loop.

**Decide on the closed bar, fill at the open of the next one.** The rule is
allowed to look at a minute only once that minute is over. It then buys at the
first price available afterwards, which is the opening price of the following
minute. The tempting alternative -- entering at the closing price of the bar
that triggered the signal -- means buying at a price that had already gone by
the time the decision existed. It is the most common way a backtest quietly
becomes fiction, and it always flatters the result.

**When a single minute contains both the stop and the target, the stop
happened first.** A minute bar records only four prices: where it opened, its
highest, its lowest, where it closed. The order those happened in is not
recorded anywhere. So when a minute's low reaches the stop *and* its high
reaches the profit target, the bar genuinely cannot say which came first, and
whichever we choose is an assumption. We choose the losing one, always, and we
**count how often we had to choose**. A backtest that assumed the good outcome
here would look better in exactly the volatile minutes where real trading is
hardest, and the count is what lets a reader judge whether the assumption
mattered at all.

**Gaps are filled at the price we would really have got.** If a minute opens
already below the stop, we do not get the stop price -- we get the opening
price, which is worse. If it opens already above the target, we get the
opening price, which is better. Both are what would actually have happened.

**Exit levels are computed from the previous minute's information.** The stop
comes from the price we were filled at, which is known. The target is "back to
the day's average price", and that average moves all day -- so the target used
while a minute is in progress is the one derived from the *previous* minute's
close. Using the same minute's average to judge that minute's high is a small
piece of look-ahead and it is avoided rather than argued about.

**Nothing is carried overnight.** Every position closes at the end of its
session at the latest, and the flat-by time closes it earlier. A position held
through a night would be exposed to news the rule never looked at.

**Every minute is recorded, not only the ones that traded.** The per-bar file
has a row for each minute the rule was consulted, including the overwhelming
majority where it said nothing. That is what allows somebody else to recompute
the rule from the record and check it. A file of only the trades is a file of
only the interesting parts, and it cannot be audited.

**Costs are a parameter, not a constant.** `cost_fraction_per_side` defaults to
zero, and a zero-cost result is not a result -- it is the ceiling. The design
requires every finding to be reported three ways: no cost, the estimated cost,
and double the estimate. If the answer changes between the estimate and double
it, we do not have a finding, and this signature is what makes producing all
three a matter of running the same function again.

**This is the share leg only.** It measures whether the rule's opinion about
SPY's share price is worth anything. The option layer -- which contract, what
it costs, what the spread takes -- sits on top and is measured separately, so
that a bad choice of contract cannot be mistaken for a bad strategy, or the
reverse. If the share leg has no edge, nothing built on it can rescue it, and
that is the cheapest possible place to find out.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import random
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .bars import Bar, BarWindow
from .params import Config
from .strategies import get as get_strategy

# What happened on a given minute. These strings land in the per-bar file, so
# they are part of the record's format and not merely internal names.
NOTHING = ""
SIGNAL = "signal"
FILLED = "filled"
NO_FILL = "no_fill"
EXIT_STOP = "stop"
EXIT_TARGET = "target"
EXIT_TIME = "time"
EXIT_FLAT_BY = "flat_by"
EXIT_SESSION_END = "session_end"

BAR_COLUMNS = [
    "t_utc", "t_et", "session", "close", "session_vwap", "distance",
    "volume_ratio", "state", "event", "entry_price", "stop", "target",
    "minutes_held", "exit_price", "note",
]

TRADE_COLUMNS = [
    "session", "signal_t_utc", "signal_close", "entry_t_utc", "entry_price",
    "exit_t_utc", "exit_price", "exit_reason", "minutes_held",
    "stop", "target_at_exit", "gross_return", "net_return", "ambiguous_exit",
]

# The design's power floor. Below this many trades the honest report is
# "underpowered", never a profit figure -- because a handful of trades cannot
# tell an edge from luck, whichever way they landed.
POWER_FLOOR = 150


@dataclass(frozen=True)
class Trade:
    session: str
    signal_t_utc: str
    signal_close: float
    entry_t_utc: str
    entry_price: float
    exit_t_utc: str
    exit_price: float
    exit_reason: str
    minutes_held: int
    stop: float
    target_at_exit: float
    gross_return: float
    net_return: float
    # True when the exit minute contained both the stop and the target, so the
    # order was assumed rather than observed. Carried per trade, not only as a
    # total, so a reader can strip these trades out and re-add the numbers.
    ambiguous_exit: bool


@dataclass
class Result:
    """Everything one run produced. Three shapes, matching three files."""

    bar_rows: List[Dict[str, object]] = field(default_factory=list)
    trades: List[Trade] = field(default_factory=list)
    summary: Dict[str, object] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_bars(path, start=None, end=None):
    """Read a share-bar CSV into Bars, and fingerprint what was read.

    The fingerprint covers the rows actually used, not the file, so narrowing
    the date range changes it. Two runs quoting the same fingerprint looked at
    the same prices; that is the only way "we reproduced it" means anything.
    """
    bars: List[Bar] = []
    digest = hashlib.sha256()
    with io.open(path, encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            session = row["session"]
            if start is not None and session < start:
                continue
            if end is not None and session > end:
                continue
            bars.append(Bar(
                t_utc=row["t_utc"],
                t_et=row["t_et"],
                session=session,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
                trades=int(row["trades"] or 0),
                vwap=float(row["vwap"] or 0.0),
            ))
            digest.update(("%s|%s|%s|%s|%s|%s\n" % (
                row["t_utc"], row["open"], row["high"], row["low"],
                row["close"], row["volume"])).encode("ascii"))
    if not bars:
        raise SystemExit("no bars in %s for %s..%s" % (path, start, end))
    return bars, digest.hexdigest()[:16]


def _minutes_of_day(bars: Sequence[Bar]) -> List[int]:
    """Each bar's New York wall-clock time as minutes past midnight.

    Used for holding time and the cooldown. Counting bars instead would be
    wrong in exactly the minutes that matter: a minute in which nothing traded
    produces no bar at all, so fifteen bars can span more than fifteen minutes.
    """
    return [int(b.t_et[11:13]) * 60 + int(b.t_et[14:16]) for b in bars]


def _as_minutes(hhmm: str) -> int:
    return int(hhmm[:2]) * 60 + int(hhmm[3:5])


# ---------------------------------------------------------------------------
# The driver
# ---------------------------------------------------------------------------


def run(bars, config=None, *, entry_mode="rule", entry_probability=0.0,
        seed=0, cost_fraction_per_side=0.0, record_bars=True):
    """Replay `bars` and return what the rule would have done.

    `entry_mode` is "rule" or "random". The random mode is the control: it
    keeps every other part of the machinery identical -- the same fill
    convention, the same stop, the same target, the same exits -- and replaces
    only the decision of *when* to enter with a coin flip. If the rule cannot
    beat that, the rule is not contributing anything, and no amount of tuning
    the exits will change it. `entry_probability` is set so the control takes
    roughly as many trades as the rule did; see `control_probability`.
    """
    config = config or Config()
    p = config.strategy_params
    decide = get_strategy(config.strategy)
    rng = random.Random(seed)

    minute = _minutes_of_day(bars)
    no_new_entry_after = _as_minutes(p.no_new_entry_after)
    flat_by = _as_minutes(config.risk.flat_by)

    window = BarWindow(bars, 0)
    rows: List[Dict[str, object]] = []
    trades: List[Trade] = []

    # Open position, held as plain locals rather than a Position: the driver is
    # the only thing that may change any of it.
    entry_index = -1
    entry_price = 0.0
    entry_t_utc = ""
    signal_t_utc = ""
    signal_close = 0.0
    stop = 0.0

    pending_index = -1        # a signal from the previous bar, awaiting a fill
    cooldown_until = -1       # minute-of-day before which we may not re-enter
    previous_vwap = None      # the day's average as of the last closed bar

    signals = 0
    no_fills = 0
    eligible = 0
    unexpressible = 0
    ambiguous = 0
    exits: Dict[str, int] = {}

    total = len(bars)
    for i in range(total):
        bar = bars[i]
        window = window.advanced_to(i)
        session_start = window.session_start
        last_of_session = (i + 1 == total) or (bars[i + 1].session != bar.session)

        if i == session_start:
            # A new day. Nothing survives the night, including a signal that
            # never got its fill.
            pending_index = -1
            cooldown_until = -1
            previous_vwap = None
            if entry_index >= 0:
                raise AssertionError("a position survived into %s" % bar.session)

        state = "holding" if entry_index >= 0 else "flat"
        event = NOTHING
        note = ""
        exit_price = ""
        target = previous_vwap * (1.0 + p.exit_distance) if previous_vwap else 0.0

        # -- 1. fill anything decided on the previous bar, at this bar's open
        if pending_index >= 0:
            entry_index = i
            entry_price = bar.open
            entry_t_utc = bar.t_utc
            stop = entry_price * (1.0 - p.stop_loss)
            cooldown_until = minute[i] + p.cooldown_minutes
            pending_index = -1
            state = "holding"
            event = FILLED

        # -- 2. exits, before any new entry, exactly as the live loop orders it
        exited = False
        if entry_index >= 0:
            held = minute[i] - minute[entry_index]
            hit_stop = bar.low <= stop
            hit_target = target > 0.0 and bar.high >= target
            ambiguous_here = hit_stop and hit_target
            if ambiguous_here:
                ambiguous += 1

            reason = ""
            price = 0.0
            if hit_stop:
                # Gapped straight through the stop? Then we did not get the
                # stop price, we got the open, and it is worse.
                price = bar.open if bar.open <= stop else stop
                reason = EXIT_STOP
            elif hit_target:
                price = bar.open if bar.open >= target else target
                reason = EXIT_TARGET
            elif held >= p.max_hold_minutes:
                price, reason = bar.close, EXIT_TIME
            elif minute[i] >= flat_by:
                price, reason = bar.close, EXIT_FLAT_BY
            elif last_of_session:
                # Should be unreachable once flat_by is inside the session, and
                # is kept because "should be" is not "is": a half day closes
                # early, and an unclosed position would otherwise cross a night.
                price, reason = bar.close, EXIT_SESSION_END

            if reason:
                gross = (price - entry_price) / entry_price
                net = gross - 2.0 * cost_fraction_per_side
                trades.append(Trade(
                    session=bar.session,
                    signal_t_utc=signal_t_utc,
                    signal_close=signal_close,
                    entry_t_utc=entry_t_utc,
                    entry_price=entry_price,
                    exit_t_utc=bar.t_utc,
                    exit_price=price,
                    exit_reason=reason,
                    minutes_held=held,
                    stop=stop,
                    target_at_exit=target,
                    gross_return=gross,
                    net_return=net,
                    ambiguous_exit=ambiguous_here,
                ))
                exits[reason] = exits.get(reason, 0) + 1
                entry_index = -1
                exited = True
                event = reason
                exit_price = price
                note = "stop and target in the same minute; stop assumed" if ambiguous_here else ""

        # -- 3. only if flat, and not in the minute an exit just fired
        distance = ""
        volume_ratio = ""
        session_vwap = window.session_vwap()
        if session_vwap:
            distance = (bar.close - session_vwap) / session_vwap
        usual = window.mean_volume(p.volume_window)
        if usual:
            volume_ratio = bar.volume / usual

        # The target is set relative to the day's average price, not relative
        # to what we paid. A position opened *above* that average is therefore
        # already past its own target, and closes in the same minute for
        # nothing. That is not a trade, and counting it as one is how the first
        # control run came back with 988 round trips of exactly 0.00%: the
        # average of a thousand zeros looks like a result and is not one.
        #
        # The rule itself can never land here, because it only buys 0.5% below
        # the average and aims at 0.1% below it. So applying the gate to both
        # is free for the rule -- its numbers have to come out identical, which
        # is the check -- and it is what turns the control from a mixture of
        # real and degenerate trades into an actual comparison.
        would_be_target = session_vwap * (1.0 + p.exit_distance) if session_vwap else 0.0
        can_enter = (
            entry_index < 0
            and not exited
            and pending_index < 0
            and not last_of_session
            and bars[i + 1].session == bar.session
            and minute[i] < no_new_entry_after
            and minute[i] >= cooldown_until
            and session_vwap is not None
            and usual is not None
        )
        if can_enter and bar.close >= would_be_target:
            # Everything else about the minute was fine; there was simply no
            # room between the price and the target for a trade to live in.
            unexpressible += 1
            can_enter = False
        if can_enter:
            eligible += 1
            if entry_mode == "random":
                fired = rng.random() < entry_probability
            else:
                fired = decide(window, None, p) is not None
            if fired:
                signals += 1
                pending_index = i
                signal_t_utc = bar.t_utc
                signal_close = bar.close
                if event == NOTHING:
                    event = SIGNAL
        elif entry_index < 0 and not exited and pending_index < 0:
            # A signal we could not act on is a fact about the strategy, not a
            # gap. The commonest cause is the last minute before the close.
            if (minute[i] < no_new_entry_after and session_vwap is not None
                    and usual is not None and minute[i] >= cooldown_until
                    and (last_of_session or bars[i + 1].session != bar.session)):
                if entry_mode == "rule" and decide(window, None, p) is not None:
                    no_fills += 1
                    signals += 1
                    event = NO_FILL
                    note = "signal on the last minute of the session; no next bar to fill on"

        if record_bars:
            rows.append({
                "t_utc": bar.t_utc,
                "t_et": bar.t_et,
                "session": bar.session,
                "close": bar.close,
                "session_vwap": "" if session_vwap is None else round(session_vwap, 6),
                "distance": "" if distance == "" else round(distance, 8),
                "volume_ratio": "" if volume_ratio == "" else round(volume_ratio, 6),
                "state": state,
                "event": event,
                "entry_price": entry_price if entry_index >= 0 or event in (
                    EXIT_STOP, EXIT_TARGET, EXIT_TIME, EXIT_FLAT_BY, EXIT_SESSION_END) else "",
                "stop": round(stop, 6) if entry_index >= 0 or exited else "",
                "target": round(target, 6) if target else "",
                "minutes_held": (minute[i] - minute[entry_index]) if entry_index >= 0 else "",
                "exit_price": exit_price,
                "note": note,
            })

        previous_vwap = session_vwap

    if entry_index >= 0:
        raise AssertionError("the run ended holding a position, which cannot happen")

    summary = _summarise(
        bars, trades, config, entry_mode, entry_probability, seed,
        cost_fraction_per_side, signals, no_fills, eligible, unexpressible,
        ambiguous, exits,
    )
    return Result(bar_rows=rows, trades=trades, summary=summary)


def _summarise(bars, trades, config, entry_mode, entry_probability, seed,
               cost, signals, no_fills, eligible, unexpressible, ambiguous,
               exits):
    p = config.strategy_params
    sessions = {b.session for b in bars}
    net = [t.net_return for t in trades]
    wins = [r for r in net if r > 0]
    losses = [r for r in net if r < 0]

    # The bar this strategy has to clear, computed from the settings rather
    # than from the outcome, so it is the same number whatever the run says.
    reward = abs(p.exit_distance - p.entry_distance)
    risk = p.stop_loss
    break_even = risk / (risk + reward) if (risk + reward) else float("nan")

    return {
        "entry_mode": entry_mode,
        "entry_probability": entry_probability,
        "seed": seed,
        "cost_fraction_per_side": cost,
        "params_hash": config.params_hash(),
        "strategy": config.strategy,
        "underlying": config.underlying,
        "feed": config.feed,
        "first_session": bars[0].session,
        "last_session": bars[-1].session,
        "sessions": len(sessions),
        "bars": len(bars),
        "eligible_minutes": eligible,
        "minutes_with_no_room_to_the_target": unexpressible,
        "signals": signals,
        "signals_unfilled": no_fills,
        "trades": len(trades),
        "trades_per_session": len(trades) / len(sessions) if sessions else 0.0,
        "underpowered": len(trades) < POWER_FLOOR,
        "power_floor": POWER_FLOOR,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(trades) if trades else float("nan"),
        "break_even_win_rate": break_even,
        "mean_net_return": sum(net) / len(net) if net else float("nan"),
        "total_net_return": sum(net),
        "mean_win": sum(wins) / len(wins) if wins else float("nan"),
        "mean_loss": sum(losses) / len(losses) if losses else float("nan"),
        "ambiguous_exit_bars": ambiguous,
        "ambiguous_share_of_trades": ambiguous / len(trades) if trades else 0.0,
        "exit_reasons": exits,
    }


def control_probability(rule_summary) -> float:
    """The coin-flip rate that gives the control about as many trades.

    A control that trades ten times against the rule's four hundred is not a
    comparison, it is a different experiment. Matching the *rate* rather than
    the exact count is deliberate: the count cannot be matched exactly, because
    entering at a random minute changes which later minutes we are free to
    enter on. The realised count is reported next to the rule's.
    """
    eligible = rule_summary["eligible_minutes"]
    return (rule_summary["signals"] / eligible) if eligible else 0.0


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def write(result: Result, out_dir: str, stem: str, extra=None) -> Dict[str, str]:
    """Three files: every minute, every trade, and the summary.

    Written with an explicit newline so a run on Windows and a run on Linux
    produce byte-identical files. The parent repository lost an afternoon to a
    reproduction check that failed only because one copy had been through git
    with different line endings.
    """
    os.makedirs(out_dir, exist_ok=True)
    paths = {}

    if result.bar_rows:
        paths["bars"] = os.path.join(out_dir, "%s_bars.csv" % stem)
        with io.open(paths["bars"], "w", encoding="utf-8", newline="\n") as handle:
            writer = csv.DictWriter(handle, fieldnames=BAR_COLUMNS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(result.bar_rows)

    paths["trades"] = os.path.join(out_dir, "%s_trades.csv" % stem)
    with io.open(paths["trades"], "w", encoding="utf-8", newline="\n") as handle:
        writer = csv.DictWriter(handle, fieldnames=TRADE_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for trade in result.trades:
            writer.writerow(asdict(trade))

    summary = dict(result.summary)
    if extra:
        summary.update(extra)
    paths["summary"] = os.path.join(out_dir, "%s_summary.json" % stem)
    with io.open(paths["summary"], "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return paths
