"""The trader. One minute at a time, from the opening bell to the close.

**The order of operations inside a minute, and why it is not negotiable.**

    1. Get the minute that just closed.
    2. If we are holding something, check whether it should be closed. If it
       should, close it -- and do not consider a new position this minute.
    3. Only if we are flat, ask the rule for an opinion, turn it into a
       contract, and put it past the risk layer.

Exits before entries, always. A loop that looks for new trades first can find
one, size it against a position count that is about to change, and end the
minute holding more than the limits allow. Nothing here closes by itself
either: every exit is an explicit decision this loop makes and writes down.

**Why the whole day's bars are re-fetched every minute** rather than appending
the newest one to a list. It costs one request and removes a class of bug. A
list built by appending drifts silently the first time a request fails, and it
drifts in the direction of disagreeing with the broker about what happened --
in exactly the situation where we most need the two to agree.

**Why it reads IEX.** Because SIP is not available on this subscription for
recent data: asking returns `403 subscription does not permit querying recent
SIP data`. IEX is one exchange of many and carries roughly four per cent of the
volume. The backtest ran on SIP. This is a real difference, it lands hardest on
the volume test, and it is reported rather than smoothed over.

**What happens when a minute goes wrong.** We ask at five seconds past, and if
the bar has not arrived we ask twice more. If it still is not there by twenty
seconds we skip the minute and *write down that we skipped it*. A minute
missing from the record and a minute in which we chose to do nothing look
identical afterwards unless the skip is recorded, and they are not the same
thing at all.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import replace
from datetime import datetime, timedelta

from .bars import BarWindow
from .broker import NEW_YORK, Broker, BrokerError, NotPaperAccount, assert_paper_account
from .contracts import ALLOWED, REFUSED, SHRUNK, Decline, Intent, Position
from .express import SHARES_PER_CONTRACT, express
from .journal import Journal
from .params import Config
from .risk import check
from .strategies.vwap_reversion import decide, target_from_vwap

# When in the minute to ask for the bar that just closed, and how patiently.
ASK_AT_SECOND = 5
RETRY_SECONDS = [7, 8]        # two more tries, giving up before second twenty
GIVE_UP_SECOND = 20


def _now_et() -> datetime:
    return datetime.now(NEW_YORK)


def _hhmm(when: datetime) -> str:
    return when.strftime("%H:%M")


def client_order_id(underlying: str, bar_t_utc: str, strategy: str, counter: int) -> str:
    """Our own name for an order, so we can always ask for it back.

    Includes a counter because one bar can legitimately produce two orders --
    an exit and, on a later minute, an entry. Without it the second collides
    with the first and the broker rejects an order we needed.
    """
    stamp = bar_t_utc.replace("-", "").replace(":", "").replace("Z", "")
    return "%s-%s-%s-%d" % (underlying, stamp, strategy, counter)


class Trader:
    """One trading day's worth of state, and the loop that advances it."""

    def __init__(self, config: Config, journal: Journal, broker: Broker,
                 dry_run: bool = False) -> None:
        self.config = config
        self.journal = journal
        self.broker = broker
        self.dry_run = dry_run

        self.position = None            # what we hold, or None
        self.pending = None             # an entry order awaiting a fill
        self.counter = 0
        self.opening_equity = 0.0
        self.entered_at_minute = None   # for the cooldown

    # -- helpers -------------------------------------------------------------

    def _next_id(self, bar_t_utc: str) -> str:
        self.counter += 1
        return client_order_id(
            self.config.underlying, bar_t_utc, self.config.strategy, self.counter
        )

    def _pnl_today(self) -> float:
        """How much we are up or down today, open positions included.

        Asked of the broker rather than accumulated ourselves. An account
        value we computed is an opinion; the account value is a fact, and the
        daily stop should bite on the fact.
        """
        try:
            return self.broker.equity() - self.opening_equity
        except BrokerError:
            # If we cannot find out, assume the worst and let the stop bite.
            # The failure mode of guessing optimistically here is trading
            # through a loss limit we cannot see.
            return -self.config.risk.daily_loss_limit

    # -- startup -------------------------------------------------------------

    def start(self, session: str) -> None:
        """Ask the world what is true before assuming anything."""
        assert_paper_account()
        account = self.broker.account()

        # Ask the broker which account this actually is. The competition is
        # judged on one specific account, and a session traded on any other one
        # counts for nothing -- so being wrong here is worse than not trading.
        actual = account.get("account_number")
        expected = self.config.expected_account
        if expected and actual != expected:
            raise NotPaperAccount(
                "refusing to trade: the broker says this is account %r, but this "
                "system is configured for %r. Check whether ALPACA_API_KEY is set "
                "in the environment -- it overrides the CLI profile silently."
                % (actual, expected)
            )

        self.opening_equity = float(account.get("equity", 0.0))
        held = self.broker.positions()

        # The broker is the only source of truth about what we hold. If the
        # process died yesterday holding something, we find out here rather
        # than by trading as though we were flat.
        for row in held:
            if row.get("asset_class") == "us_option":
                self.position = Position(
                    contract=row["symbol"],
                    quantity=int(float(row["qty"])),
                    entry_premium=float(row.get("avg_entry_price", 0.0)),
                    entry_t_utc="",
                    underlying_at_entry=0.0,
                )

        self.journal.session_event("start", {
            "equity": self.opening_equity,
            "account_number": actual,
            "underlying": self.config.underlying,
            "status": account.get("status"),
            "options_approved_level": account.get("options_approved_level"),
            "positions_found_at_start": held,
            "adopted_position": self.position.contract if self.position else None,
            "feed": self.broker.feed,
            "dry_run": self.dry_run,
            "limits": {
                "max_premium_per_trade": self.config.risk.max_premium_per_trade,
                "max_open_positions": self.config.risk.max_open_positions,
                "daily_loss_limit": self.config.risk.daily_loss_limit,
                "flat_by": self.config.risk.flat_by,
            },
        })

    # -- one minute ----------------------------------------------------------

    def on_bar(self, window: BarWindow) -> None:
        bar = window.current
        now = _hhmm(_now_et())

        # An entry order from last minute either filled or it did not. Settle
        # that before anything else, so the rest of the minute reasons about a
        # position we actually hold.
        if self.pending is not None:
            self._settle_pending(bar)

        if self.position is not None:
            self._manage_position(window, now)
            return

        view = decide(window, None, self.config.strategy_params)
        if view is None:
            self.journal.decision(
                bar.t_utc, bar.t_et, bar.close, "no_signal",
                "Nothing to do: the price is not far enough below today's average, "
                "or this minute was not busy enough.",
                {"close": bar.close, "session_vwap": window.session_vwap() or 0.0},
            )
            return

        self._try_to_enter(window, view, now)

    # -- exits ---------------------------------------------------------------

    def _manage_position(self, window: BarWindow, now: str) -> None:
        """Should we still be holding this? Checked every single minute."""
        bar = window.current
        position = self.position
        params = self.config.strategy_params
        session_vwap = window.session_vwap() or bar.close

        stop = position.underlying_at_entry * (1.0 - params.stop_loss)
        target = target_from_vwap(session_vwap, params)
        held = self._minutes_held(bar)

        reason = None
        if now >= self.config.risk.flat_by:
            reason = ("It is %s and everything is closed by %s. Selling regardless of "
                      "whether the trade worked." % (now, self.config.risk.flat_by))
            action = "exit_flat_by"
        elif position.underlying_at_entry > 0 and bar.low <= stop:
            # Stop before target when one minute reached both -- the same
            # pessimistic convention the backtest uses, so the two agree.
            reason = ("SPY fell to %.2f, through our stop at %.2f. Taking the loss."
                      % (bar.low, stop))
            action = "exit_stop"
        elif bar.high >= target:
            reason = ("SPY reached %.2f, back at today's average. Taking the profit."
                      % (bar.high,))
            action = "exit_target"
        elif held >= params.max_hold_minutes:
            reason = ("Held %d minutes without hitting either the target or the stop. "
                      "The bet was that it bounces quickly; it did not." % held)
            action = "exit_time"

        if reason is None:
            self.journal.decision(
                bar.t_utc, bar.t_et, bar.close, "holding",
                "Still holding %d of %s. SPY %.2f, stop %.2f, target %.2f, %d minutes in."
                % (position.quantity, position.contract, bar.close, stop, target, held),
                {"stop": stop, "target": target, "minutes_held": float(held)},
                contract=position.contract,
            )
            return

        self._close(position, bar, action, reason, {"stop": stop, "target": target,
                                                    "minutes_held": float(held)})

    def sample_equity(self, bar) -> None:
        """Write down what the account is worth, once a minute.

        Not decoration. While an option position is open, the account's value
        moves with that contract's own price, so a curve drawn from real
        samples shows the dip in the middle of a trade -- exactly the part an
        opening-and-closing pair hides. Two numbers a day cannot be drawn as a
        line without inventing everything between them.

        A failed read is journalled and skipped. A missing point on a chart
        must never be a reason to stop trading.
        """
        try:
            account = self.broker.account()
        except BrokerError as exc:
            self.journal.session_event(
                "equity_unavailable", {"bar_t_et": bar.t_et, "error": str(exc)})
            return
        self.journal.session_event("equity", {
            "bar_t_et": bar.t_et,
            "bar_t_utc": bar.t_utc,
            "equity": float(account.get("equity", 0.0) or 0.0),
            "cash": float(account.get("cash", 0.0) or 0.0),
            "holding": self.position.contract if self.position else None,
        })

    def _minutes_held(self, bar) -> int:
        """Wall-clock minutes, never a count of bars.

        A minute in which nothing trades produces no bar at all, so counting
        bars would let a position sit open far longer than the rule allows --
        in exactly the illiquid stretches where that is most dangerous.
        """
        if not self.position or not self.position.entry_t_utc:
            return 0
        entered = datetime.strptime(self.position.entry_t_utc[:19], "%Y-%m-%dT%H:%M:%S")
        now = datetime.strptime(bar.t_utc[:19], "%Y-%m-%dT%H:%M:%S")
        return int((now - entered).total_seconds() // 60)

    def _close(self, position, bar, action, reason, evidence) -> None:
        order_id = self._next_id(bar.t_utc)
        request = {"contract": position.contract, "quantity": position.quantity,
                   "side": "sell", "type": "market"}
        try:
            response = self.broker.sell_to_close(
                position.contract, position.quantity, order_id, dry_run=self.dry_run
            )
            self.journal.order("sell_to_close", order_id, request, response)
            self.position = None
        except BrokerError as exc:
            # Ask before assuming. A request that timed out may have succeeded.
            found = self.broker.order_by_client_id(order_id)
            self.journal.order("sell_to_close", order_id, request, found, error=str(exc))
            if found is None:
                # It really did not go through. Stay holding, say so, and try
                # again next minute -- the flattener is the backstop.
                self.journal.decision(
                    bar.t_utc, bar.t_et, bar.close, "exit_failed",
                    "Tried to sell %s and could not: %s. Still holding; will retry."
                    % (position.contract, exc), evidence, contract=position.contract,
                )
                return
            self.position = None

        self.journal.decision(bar.t_utc, bar.t_et, bar.close, action, reason,
                              evidence, contract=position.contract)

    # -- entries -------------------------------------------------------------

    def _try_to_enter(self, window: BarWindow, view, now: str) -> None:
        bar = window.current
        params = self.config.strategy_params
        session_vwap = window.session_vwap() or bar.close

        # The cooldown. Measured from the last entry, not the last exit, so a
        # long trade does not earn a longer wait afterwards.
        if self.entered_at_minute is not None:
            gap = (datetime.strptime(bar.t_utc[:19], "%Y-%m-%dT%H:%M:%S")
                   - self.entered_at_minute).total_seconds() / 60.0
            if gap < params.cooldown_minutes:
                self.journal.decision(
                    bar.t_utc, bar.t_et, bar.close, "cooldown",
                    "The rule fired, but we entered only %.0f minutes ago and wait %d."
                    % (gap, params.cooldown_minutes), view.evidence,
                )
                return

        expiry = self._nearest_expiry(bar.session)
        band = self.config.expression.strike_offset
        try:
            quotes = self.broker.option_chain(
                expiry, "call" if view.direction == "up" else "put",
                bar.close + band - 5.0, bar.close + band + 5.0,
            )
        except BrokerError as exc:
            self.journal.decision(
                bar.t_utc, bar.t_et, bar.close, "no_chain",
                "The rule fired but the option prices could not be fetched: %s" % exc,
                view.evidence,
            )
            return

        stop = bar.close * (1.0 - params.stop_loss)
        target = target_from_vwap(session_vwap, params)
        proposal = express(
            view, quotes, self.config.underlying, bar.close, stop, target,
            self.config.risk.max_premium_per_trade, self.config.expression,
        )

        if isinstance(proposal, Decline):
            self.journal.decision(
                bar.t_utc, bar.t_et, bar.close, "declined", proposal.reason,
                dict(view.evidence, **proposal.evidence),
            )
            return

        verdict = check(
            proposal, self.config.risk,
            open_positions=1 if self.position else 0,
            profit_and_loss_today=self._pnl_today(),
            now_et=now,
        )

        if verdict.outcome == REFUSED:
            self.journal.decision(
                bar.t_utc, bar.t_et, bar.close, "refused",
                "The trade was ready and the risk layer said no: %s" % verdict.reason,
                dict(view.evidence, **proposal.evidence), contract=proposal.contract,
            )
            return

        intent = verdict.intent
        if verdict.outcome == SHRUNK:
            self.journal.decision(
                bar.t_utc, bar.t_et, bar.close, "shrunk",
                "Order reduced before sending: %s" % verdict.reason,
                dict(view.evidence, **proposal.evidence), contract=intent.contract,
            )

        self._send_entry(bar, intent, view)

    def _send_entry(self, bar, intent: Intent, view) -> None:
        order_id = self._next_id(bar.t_utc)
        request = {"contract": intent.contract, "quantity": intent.quantity,
                   "side": "buy", "type": "limit", "limit_price": intent.limit_price}
        try:
            response = self.broker.buy_to_open(
                intent.contract, intent.quantity, intent.limit_price,
                order_id, dry_run=self.dry_run,
            )
            self.journal.order("buy_to_open", order_id, request, response)
        except BrokerError as exc:
            found = self.broker.order_by_client_id(order_id)
            self.journal.order("buy_to_open", order_id, request, found, error=str(exc))
            if found is None:
                self.journal.decision(
                    bar.t_utc, bar.t_et, bar.close, "entry_failed",
                    "Wanted to buy %s and the order did not go through: %s"
                    % (intent.contract, exc), intent.evidence,
                )
                return

        self.journal.decision(
            bar.t_utc, bar.t_et, bar.close, "ordered", intent.reason,
            intent.evidence, contract=intent.contract, client_order_id=order_id,
        )
        if self.dry_run:
            # Nothing was actually sent, so there is nothing to settle. The
            # rehearsal's whole purpose is to prove the request body is right.
            return
        self.pending = (order_id, intent, bar.close)

    def _settle_pending(self, bar) -> None:
        """Did last minute's limit order fill, or should it be pulled?

        A limit that has not filled within the minute is cancelled and the
        trade is simply not taken. That miss is counted: a strategy that only
        works when we chase the price is a strategy we do not have.
        """
        order_id, intent, underlying_at_signal = self.pending
        self.pending = None
        order = self.broker.order_by_client_id(order_id)

        if order and order.get("status") == "filled":
            filled_at = float(order.get("filled_avg_price") or intent.limit_price)
            quantity = int(float(order.get("filled_qty") or intent.quantity))
            self.position = Position(
                contract=intent.contract,
                quantity=quantity,
                entry_premium=filled_at,
                entry_t_utc=bar.t_utc,
                underlying_at_entry=underlying_at_signal,
            )
            self.entered_at_minute = datetime.strptime(bar.t_utc[:19], "%Y-%m-%dT%H:%M:%S")
            self.journal.decision(
                bar.t_utc, bar.t_et, bar.close, "entered",
                "Filled: %d of %s at $%.2f, $%.0f at risk."
                % (quantity, intent.contract, filled_at, quantity * filled_at * SHARES_PER_CONTRACT),
                {"filled_avg_price": filled_at, "quantity": float(quantity)},
                contract=intent.contract,
            )
            return

        try:
            self.broker.cancel_all_orders()
        except BrokerError:
            pass
        self.journal.decision(
            bar.t_utc, bar.t_et, bar.close, "missed",
            "The order for %s did not fill at $%.2f within the minute, so the trade "
            "was not taken. We do not chase." % (intent.contract, intent.limit_price),
            {"limit_price": intent.limit_price, "status": (order or {}).get("status", "unknown")},
            contract=intent.contract,
        )

    def _nearest_expiry(self, session: str) -> str:
        """The expiry `target_days_to_expiry` days out, or the next one after.

        SPY lists contracts expiring every weekday, so this is nearly always
        exactly the day asked for; the weekend roll is the exception it exists
        to handle.
        """
        day = datetime.strptime(session, "%Y-%m-%d") + timedelta(
            days=self.config.expression.target_days_to_expiry
        )
        while day.weekday() >= 5:
            day += timedelta(days=1)
        return day.strftime("%Y-%m-%d")


MAX_WAIT_FOR_OPEN_SECONDS = 4 * 60 * 60


def _seconds_until_open(clock):
    """How long until the bell, or None if that is not a question we can answer.

    Alpaca reports `next_open` as a full timestamp with its offset attached.
    A value in the past, or one we cannot read, returns None -- the caller
    treats that as "do not wait", because guessing about market hours is how a
    system ends up trading a holiday.
    """
    raw = clock.get("next_open")
    if not raw:
        return None
    try:
        opens_at = datetime.fromisoformat(raw)
    except ValueError:
        return None
    seconds = (opens_at - datetime.now(opens_at.tzinfo)).total_seconds()
    return seconds if seconds > 0 else None


def run(config: Config, journal_root: str, dry_run: bool = False,
        max_minutes: int = 0) -> int:
    """Run one trading session, minute by minute, until the close.

    Returns the number of minutes actually processed, so a caller -- or a test
    -- can tell a session that ran from one that fell over in the first minute.
    """
    broker = Broker(feed=config.feed, underlying=config.underlying)
    clock = broker.clock()
    session = _now_et().strftime("%Y-%m-%d")

    journal = Journal(journal_root, session, config.params_hash(), config.version)
    trader = Trader(config, journal, broker, dry_run=dry_run)

    if not clock.get("is_open"):
        seconds = _seconds_until_open(clock)
        if seconds is None or seconds > MAX_WAIT_FOR_OPEN_SECONDS:
            journal.session_event("not_open", {"clock": clock})
            print("The market is closed and does not open again soon, so there is "
                  "nothing to trade.")
            print("  next open : %s" % clock.get("next_open"))
            print("  now       : %s New York" % _now_et().strftime("%H:%M:%S"))
            return 0
        # Started early. Wait rather than exit: a trader that quits silently
        # because it was launched ten minutes before the bell costs a whole
        # session, and nobody finds out until the session is over.
        journal.session_event("waiting_for_open", {
            "next_open": clock.get("next_open"),
            "seconds_to_wait": round(seconds),
        })
        print("The market opens in %d minutes. Waiting rather than exiting -- "
              "leave this running." % round(seconds / 60.0))
        time.sleep(seconds + 2.0)

    trader.start(session)
    print("Trading %s on the %s feed, %s." % (
        config.underlying, config.feed,
        "DRY RUN -- no orders will be sent" if dry_run else "live"))
    print("  account   : %s" % config.expected_account)
    print("  equity    : $%s" % "{:,.2f}".format(trader.opening_equity))
    print("  per trade : $%s at risk, %d position(s) at once, flat by %s New York" % (
        "{:,.0f}".format(config.risk.max_premium_per_trade),
        config.risk.max_open_positions, config.risk.flat_by))
    print("")
    processed = 0
    last_seen = None

    while True:
        now = _now_et()
        if _hhmm(now) >= "16:00":
            break
        if max_minutes and processed >= max_minutes:
            break

        # Wait for the top of the next minute, plus a few seconds for the bar
        # to be published.
        target = (now + timedelta(minutes=1)).replace(second=ASK_AT_SECOND, microsecond=0)
        time.sleep(max(0.0, (target - _now_et()).total_seconds()))

        bars = []
        for attempt, second in enumerate([ASK_AT_SECOND] + RETRY_SECONDS):
            try:
                bars = broker.session_bars(session)
            except BrokerError:
                bars = []
            if bars and bars[-1].t_utc != last_seen:
                break
            if _now_et().second >= GIVE_UP_SECOND:
                break
            time.sleep(1.0)

        if not bars or bars[-1].t_utc == last_seen:
            journal.decision(
                "", _now_et().strftime("%Y-%m-%dT%H:%M:%S"), 0.0, "skipped_no_bar",
                "No new price arrived in time for this minute, so no decision was "
                "made. Recorded so a gap in the data cannot be mistaken for a "
                "deliberate silence.", {},
            )
            continue

        last_seen = bars[-1].t_utc
        window = BarWindow(bars, len(bars) - 1)
        try:
            trader.on_bar(window)
            trader.sample_equity(window.current)
        except NotPaperAccount as exc:
            journal.session_event("halted", {"reason": str(exc)})
            raise
        except BrokerError as exc:
            journal.decision(
                bars[-1].t_utc, bars[-1].t_et, bars[-1].close, "error",
                "Something went wrong this minute and was skipped: %s" % exc, {},
            )
        processed += 1

    journal.session_event("end", {
        "minutes_processed": processed,
        "closing_equity": broker.equity(),
        "opening_equity": trader.opening_equity,
        "still_holding": trader.position.contract if trader.position else None,
    })
    return processed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--journal", default="journal",
                        help="where to write the record (default: journal/)")
    parser.add_argument("--dry-run", action="store_true",
                        help="build and print every order without sending any")
    parser.add_argument("--quarter-size", action="store_true",
                        help="the pre-committed fallback: every money limit divided "
                             "by four, one position at a time")
    parser.add_argument("--max-minutes", type=int, default=0,
                        help="stop after this many minutes (for a short rehearsal)")
    args = parser.parse_args(argv)

    config = Config()
    if args.quarter_size:
        config = replace(config, risk=config.risk.reduced())

    processed = run(config, args.journal, dry_run=args.dry_run,
                    max_minutes=args.max_minutes)
    print("processed %d minutes" % processed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
