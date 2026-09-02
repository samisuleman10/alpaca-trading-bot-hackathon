"""The only file allowed to talk to Alpaca.

Not written yet -- it is a Phase 3 file. What is here is the list of rules it
must obey, written down now, while we know why each one exists. A constraint
recorded a week before the code is a design; the same constraint recalled on
the morning it matters is a guess.

Every rule below is either something we measured ourselves with a probe, or
something Alpaca's own published agent guidance (alpacahq/alpaca-skills,
commit 62891ec, Apache-2.0) says, which we then checked against our own
observations. Where the two disagree, the measurement wins and the
disagreement is noted.

--------------------------------------------------------------------------
1. Prove it is the paper account, before every single order
--------------------------------------------------------------------------
Run `alpaca doctor` and require its `Trading:` line to read
`https://paper-api.alpaca.markets`. Anything else -- including a failure to
answer -- and the order is not placed. Not a warning: a stop.

This is stronger than checking that the API key starts with `PK` rather than
`AK`, because it asks what the tool is actually pointed at rather than what we
believe we configured. A key can be right while the profile in force is not.

--------------------------------------------------------------------------
2. Never pass `-p` or `--profile` to any command
--------------------------------------------------------------------------
`alpaca doctor` ignores that flag. So a run that passes `--profile paper` to
the order and `--profile paper` to the doctor gets a safety check performed on
one account and an order placed on another -- the check passes and means
nothing. Select the account once, for the whole process, with the
`ALPACA_PROFILE` environment variable, so both commands cannot diverge.

This is the single most dangerous item on this page, because the failure is
silent and looks like success.

--------------------------------------------------------------------------
3. Do not wrap the CLI in our own retry loop for rate limits
--------------------------------------------------------------------------
The CLI already retries a rate limit (HTTP 429) and a server error three times
and honours the `Retry-After` header telling it how long to wait. A second
layer of backoff on top multiplies the two together and turns a two-second
pause into a stall long enough to miss the minute entirely. If a command still
fails after the CLI's own retries, surface it and stop.

The whole-download restart loop in `scripts/download_all_bars.py` is not this:
it retries an entire multi-hour job that died, not an individual request, and
it never sees a 429 the CLI has not already given up on.

--------------------------------------------------------------------------
4. `--dry-run` prints the exact request body without sending anything
--------------------------------------------------------------------------
This is how the expression and risk layers get verified against reality rather
than against our reading of the documentation. Every order the system builds
during the dry run goes through `--dry-run` first and the printed body is
saved. If what we think we are sending is not what we are sending, that is
where it shows up -- on Monday, not Tuesday.

--------------------------------------------------------------------------
5. JSON is already the default output; `--quiet` is not the JSON switch
--------------------------------------------------------------------------
`--quiet` only drops warnings, hints and colour. Filter with the CLI's built-in
`--jq` flag rather than piping to an external `jq`, which is one fewer thing
that has to be installed on the server. Never parse `--csv`: it is for humans.

--------------------------------------------------------------------------
6. Two account fields that do not exist
--------------------------------------------------------------------------
`pattern_day_trader` and `daytrade_count` are not on the Trading API account
object, whatever a search result may say. Whether the account is treated as a
pattern day trader -- someone making several same-day round trips a week, who
must hold $25,000 to keep doing it -- is inferred from `multiplier` being `4`.

Our paper account holds $100,000, so the rule does not bite. It is recorded
because a system that day-trades and never checks this is one funding change
away from having its orders rejected for a reason nobody wrote down.

--------------------------------------------------------------------------
7. Ask the clock; never assume the market is open
--------------------------------------------------------------------------
`alpaca clock` answers it. Half days exist -- the market closed at 13:00 on
three days in 2024 -- and the data feed keeps emitting bars after the close
from after-hours trading, so "there is a bar, therefore the market is open" is
false. The downloader already handles this by using the official calendar; the
live trader must do the same thing through the clock.

--------------------------------------------------------------------------
8. Exit codes and stderr
--------------------------------------------------------------------------
`0` success, `1` error, `2` authentication failure. Check the code; a command
that printed something is not a command that worked. `2` in particular means
the credentials are wrong or expired, which is a stop-everything condition and
not a retry.

--------------------------------------------------------------------------
9. Build the client order id ourselves, always
--------------------------------------------------------------------------
Ours is symbol + bar timestamp + strategy + a counter, because one bar can
legitimately produce two orders. On any timeout, ask the broker for the order
by our own id before doing anything else -- a request that timed out may well
have succeeded, and the only way to find out is to ask. Probe 3 proved the
adjacent version of this: a `200` from `position close-all` means *accepted*,
not *closed*, with the order still `pending_new` and nothing filled. So the
flattener calls, verifies, and calls again.

--------------------------------------------------------------------------
10. Where we knowingly depart from Alpaca's guidance
--------------------------------------------------------------------------
Their skill requires a human to confirm each order, defaulting to on. We are
building an autonomous trader, which is the premise of the competition, so
there is no human in the loop at 10:47 on a Tuesday.

The obligation is met earlier and harder instead. The limits -- $1,000 of
premium per trade, two open positions, a $2,000 daily stop, flat by 15:45,
buy contracts only -- were approved by name before any code was written, and
they are enforced in `risk.py`, which can only ever answer no or smaller. The
endpoint check in rule 1 runs before every order without anyone having to
remember it. A gate in code that cannot be skipped is a stronger guarantee
than a prompt that can be clicked through, and it is the only kind available
to a program running unattended.

They also require every order to be gated behind an explicit confirmation when
it is unscoped and destructive -- `order cancel-all`, `position close-all`.
Our flattener runs exactly those two commands, unattended, five minutes after
the close. That is deliberate and it is the safer choice: the thing being
prevented is a same-day option contract expiring into 100 actual shares per
contract, roughly $77,000 that was never budgeted for. The account it runs
against trades nothing but this system, so there is no third party's position
for it to close by accident.
"""

# ---------------------------------------------------------------------------
# 11. Added 2026-09-01, after a probe rather than from the documentation
# ---------------------------------------------------------------------------
# The live trader reads **IEX**, not SIP. Asking for a recent SIP bar on this
# subscription returns `403 subscription does not permit querying recent SIP
# data`; IEX answers in real time, to the second.
#
# This matters and is not cosmetic. IEX is one exchange among many and carries
# roughly four per cent of the volume, so a rule that asks "was this minute
# busier than usual?" is asking a different question live than it asked in the
# backtest, which ran on SIP. params.py predicted exactly this failure before
# any of it was written. The honest handling is: run live on IEX, re-run the
# backtest on the IEX files we already have, and report both. Not to quietly
# use one number and cite the other.

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

from zoneinfo import ZoneInfo

from .bars import Bar

NEW_YORK = ZoneInfo("America/New_York")

ALPACA = "alpaca"

# Rule 1. The one string that decides whether an order is pretend money.
PAPER_TRADING_HOST = "https://paper-api.alpaca.markets"

# Rule 8. The CLI's exit codes.
EXIT_OK = 0
EXIT_AUTH = 2


class BrokerError(RuntimeError):
    """Any failure to get a straight answer out of Alpaca."""


class AuthError(BrokerError):
    """Exit code 2: the credentials are wrong or expired.

    Deliberately its own type. This is a stop-everything condition, never
    something to retry -- retrying bad credentials just makes the same mistake
    faster.
    """


class NotPaperAccount(BrokerError):
    """The endpoint in force is not the paper one.

    If this is ever raised in anger, something is pointed at real money and
    the correct response is to place no order at all.
    """


def _run(args: Sequence[str], timeout: int = 30) -> Any:
    """Run one Alpaca command and hand back the parsed JSON.

    No retry loop lives here, on purpose: rule 3. The CLI already retries rate
    limits and server errors three times and honours the wait it is told to
    wait. A second layer on top multiplies the two and turns a two-second pause
    into a missed minute.
    """
    try:
        done = subprocess.run(
            [ALPACA] + list(args),
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise BrokerError("alpaca %s timed out after %ds" % (" ".join(args), timeout)) from exc

    if done.returncode == EXIT_AUTH:
        raise AuthError("alpaca %s: authentication failed" % (" ".join(args),))
    if done.returncode != EXIT_OK:
        raise BrokerError(
            "alpaca %s exited %d: %s" % (" ".join(args), done.returncode, done.stderr.strip())
        )

    text = done.stdout.strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise BrokerError("alpaca %s did not return JSON: %s" % (" ".join(args), text[:200])) from exc

    # An HTTP error can arrive with exit code 0 and an error object in the
    # body -- that is how the SIP 403 announced itself. A command that printed
    # something is not a command that worked (rule 8), so check the body too.
    if isinstance(payload, dict) and payload.get("error") and payload.get("status"):
        raise BrokerError(
            "alpaca %s: HTTP %s %s" % (" ".join(args), payload["status"], payload["error"])
        )
    return payload


def assert_paper_account() -> None:
    """Rule 1, and the only thing standing between this system and real money.

    Asks the tool what it is actually pointed at rather than trusting what we
    believe we configured. Costs about 1.2 seconds, runs before every order,
    and refuses rather than warns.
    """
    try:
        done = subprocess.run([ALPACA, "doctor"], capture_output=True, text=True, timeout=20)
    except subprocess.TimeoutExpired as exc:
        raise NotPaperAccount("could not confirm the account is paper: doctor timed out") from exc

    for line in done.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("Trading:"):
            host = stripped.split(":", 1)[1].strip()
            if host != PAPER_TRADING_HOST:
                raise NotPaperAccount(
                    "refusing to trade: endpoint is %r, not the paper endpoint %r"
                    % (host, PAPER_TRADING_HOST)
                )
            return
    raise NotPaperAccount("refusing to trade: doctor named no trading endpoint")


@dataclass(frozen=True)
class Quote:
    """One option contract's current buying and selling price.

    `bid` is what someone will pay us for it, `ask` is what we must pay to get
    one. The gap between them is the spread, and it is a real cost paid twice
    -- once entering and once leaving -- because we buy at the ask and sell at
    the bid. `mid` is the fair-ish price in between.
    """

    contract: str
    right: str
    strike: float
    expiry: str
    bid: float
    ask: float
    bid_size: float
    ask_size: float
    t_utc: str

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    @property
    def spread(self) -> float:
        return self.ask - self.bid

    @property
    def spread_fraction(self) -> float:
        """The spread as a share of the contract's own price.

        A five-cent gap is trivial on a $20 contract and a third of the money
        on a fifteen-cent one, so the fraction is what the gate reads, never
        the raw gap. Returns 1.0 -- the worst possible answer -- when there is
        no usable price, so a missing quote can never look cheap.
        """
        if self.mid <= 0.0:
            return 1.0
        return self.spread / self.mid


def parse_contract(symbol: str) -> Dict[str, Any]:
    """Pull the parts back out of a contract symbol.

    `SPY260904C00770000` is a call on SPY expiring 2026-09-04 at a strike of
    $770. The last eight digits are the strike in thousandths of a dollar.
    """
    strike_raw = symbol[-8:]
    right = symbol[-9]
    date_raw = symbol[-15:-9]
    underlying = symbol[:-15]
    expiry = "20%s-%s-%s" % (date_raw[0:2], date_raw[2:4], date_raw[4:6])
    return {
        "underlying": underlying,
        "expiry": expiry,
        "right": "call" if right == "C" else "put",
        "strike": int(strike_raw) / 1000.0,
    }


def _bar_from_json(row: Dict[str, Any]) -> Bar:
    """Turn one of Alpaca's bars into ours, carrying the New York clock along.

    The timestamp is the moment the minute *began* in Alpaca's data. Everything
    downstream treats a bar as the minute that has closed, and the New York
    reading is carried rather than recomputed so a rule asking "is it past
    15:40?" never has to do timezone arithmetic of its own.
    """
    started = datetime.strptime(row["t"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    in_new_york = started.astimezone(NEW_YORK)
    return Bar(
        t_utc=started.strftime("%Y-%m-%dT%H:%M:%SZ"),
        t_et=in_new_york.strftime("%Y-%m-%dT%H:%M:%S"),
        session=in_new_york.strftime("%Y-%m-%d"),
        open=float(row["o"]),
        high=float(row["h"]),
        low=float(row["l"]),
        close=float(row["c"]),
        volume=float(row["v"]),
        trades=int(row.get("n", 0)),
        vwap=float(row.get("vw", 0.0)),
    )


class Broker:
    """Everything the live trader is allowed to ask of the outside world.

    Nothing else in `src/agent/` imports subprocess or touches the network. If
    a future change needs a new question asked of Alpaca, it is added here or
    it does not happen -- which is what makes "the broker is the only source of
    truth" a checkable claim rather than an intention.
    """

    def __init__(self, feed: str = "iex", underlying: str = "SPY") -> None:
        self.feed = feed
        self.underlying = underlying

    # -- what time is it, and is anyone trading -----------------------------

    def clock(self) -> Dict[str, Any]:
        """Rule 7: ask, never assume.

        Half days exist -- the market shut at 13:00 on three days in 2024 --
        and the feed keeps emitting bars after the close from after-hours
        trading. "There is a bar, therefore the market is open" is false.
        """
        return _run(["clock"])

    def is_open(self) -> bool:
        return bool(self.clock().get("is_open"))

    # -- prices --------------------------------------------------------------

    def session_bars(self, session: str, symbol: Optional[str] = None) -> List[Bar]:
        """Every minute of one trading day so far, oldest first.

        The whole day is re-fetched each minute rather than appending the
        newest bar to a running list. It costs one request and removes a whole
        class of bug: a list built by appending drifts silently the first time
        a request fails, and it disagrees with the broker about history in
        exactly the situation where we most need them to agree.
        """
        symbol = symbol or self.underlying
        start = datetime.strptime(session, "%Y-%m-%d").replace(
            hour=9, minute=30, tzinfo=NEW_YORK
        )
        rows: List[Dict[str, Any]] = []
        token = None
        while True:
            args = [
                "data", "bars", "--symbol", symbol, "--feed", self.feed,
                "--timeframe", "1Min", "--limit", "10000",
                "--start", start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            ]
            if token:
                args += ["--page-token", token]
            payload = _run(args) or {}
            rows.extend(payload.get("bars") or [])
            token = payload.get("next_page_token")
            if not token:
                break

        bars = [_bar_from_json(row) for row in rows]
        # After-hours bars belong to the same calendar date and must not reach
        # the rule: it would form opinions about a market nobody is trading.
        return [b for b in bars if "09:30:00" <= b.t_et[11:19] < "16:00:00" and b.session == session]

    def listed_expiries(self, on_or_after: str, through: str) -> List[str]:
        """Which expiry dates actually exist for our underlying, in order.

        We used to compute the expiry from the calendar -- tomorrow, skipping
        weekends -- and assume a contract existed for it. That holds for SPY on
        an ordinary week and fails everywhere else: DIA and SLV list weekly
        Fridays, not every weekday, and no underlying at all lists an expiry on
        a market holiday. Asking is one request and cannot be wrong.
        """
        seen, token = set(), None
        while True:
            cmd = [
                "option", "contracts",
                "--underlying-symbols", self.underlying,
                "--expiration-date-gte", on_or_after,
                "--expiration-date-lte", through,
                "--type", "call",
                "--limit", "10000",
            ]
            if token:
                cmd += ["--page-token", token]
            payload = _run(cmd) or {}
            for row in payload.get("option_contracts") or []:
                if row.get("expiration_date"):
                    seen.add(row["expiration_date"])
            token = payload.get("next_page_token")
            if not token:
                return sorted(seen)

    def option_chain(
        self,
        expiry: str,
        right: str,
        strike_low: float,
        strike_high: float,
    ) -> List[Quote]:
        """Live bid and ask for every contract in a strike band on one expiry.

        Narrowed by strike on purpose. The chain runs to hundreds of contracts
        and we only ever want the handful near today's price, so asking for the
        band is one request instead of paging through the lot.
        """
        payload = _run([
            "data", "option", "chain",
            "--underlying-symbol", self.underlying,
            "--expiration-date", expiry,
            "--type", right,
            "--strike-price-gte", "%.2f" % strike_low,
            "--strike-price-lte", "%.2f" % strike_high,
            "--limit", "200",
        ]) or {}

        quotes = []
        for symbol, snapshot in (payload.get("snapshots") or {}).items():
            quote = snapshot.get("latestQuote") or {}
            if not quote:
                continue
            parts = parse_contract(symbol)
            quotes.append(Quote(
                contract=symbol,
                right=parts["right"],
                strike=parts["strike"],
                expiry=parts["expiry"],
                bid=float(quote.get("bp", 0.0)),
                ask=float(quote.get("ap", 0.0)),
                bid_size=float(quote.get("bs", 0.0)),
                ask_size=float(quote.get("as", 0.0)),
                t_utc=str(quote.get("t", "")),
            ))
        return sorted(quotes, key=lambda q: q.strike)

    def latest_option_quote(self, contract: str) -> Optional[Quote]:
        """What one contract we already hold is worth right now."""
        payload = _run([
            "data", "option", "latest-quotes", "--symbols", contract,
        ]) or {}
        quote = (payload.get("quotes") or {}).get(contract)
        if not quote:
            return None
        parts = parse_contract(contract)
        return Quote(
            contract=contract, right=parts["right"], strike=parts["strike"],
            expiry=parts["expiry"],
            bid=float(quote.get("bp", 0.0)), ask=float(quote.get("ap", 0.0)),
            bid_size=float(quote.get("bs", 0.0)), ask_size=float(quote.get("as", 0.0)),
            t_utc=str(quote.get("t", "")),
        )

    # -- the account ---------------------------------------------------------

    def account(self) -> Dict[str, Any]:
        return _run(["account", "get"]) or {}

    def equity(self) -> float:
        """What the account is actually worth, asked rather than assumed.

        risk.py sizes against this. Hardcoding $100,000 would mean the limits
        stop meaning what they say the moment the account's value moves.
        """
        return float(self.account().get("equity", 0.0))

    def positions(self) -> List[Dict[str, Any]]:
        """What we actually hold, according to the only party who knows.

        Called on every start. Our own record of what we hold is a convenience;
        this is the truth, and where they disagree this wins.
        """
        return _run(["position", "list"]) or []

    # -- orders --------------------------------------------------------------

    def buy_to_open(
        self,
        contract: str,
        quantity: int,
        limit_price: float,
        client_order_id: str,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Buy option contracts. The only way this system opens anything.

        Named for what it does rather than taking a `side` argument, so that
        "we never sell a contract we do not own" is visible in the shape of the
        interface instead of being a flag somebody could pass wrongly.

        A limit order, never a market order: a market order on an option with a
        wide spread accepts whatever price is there, which is precisely the
        cost the expression layer exists to refuse.
        """
        assert_paper_account()
        args = [
            "order", "submit",
            "--symbol", contract,
            "--qty", str(quantity),
            "--side", "buy",
            "--type", "limit",
            "--limit-price", "%.2f" % limit_price,
            "--time-in-force", "day",
            "--client-order-id", client_order_id,
        ]
        if dry_run:
            args.append("--dry-run")
        return _run(args) or {}

    def sell_to_close(
        self,
        contract: str,
        quantity: int,
        client_order_id: str,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Sell contracts we already hold. Never opens a short.

        A market order here, unlike the entry. Getting out is not optional: a
        limit that does not fill leaves a position open past the time we said
        everything would be closed, which is the failure the whole flat-by rule
        exists to prevent. We accept the spread to guarantee the exit.
        """
        assert_paper_account()
        args = [
            "order", "submit",
            "--symbol", contract,
            "--qty", str(quantity),
            "--side", "sell",
            "--type", "market",
            "--time-in-force", "day",
            "--client-order-id", client_order_id,
        ]
        if dry_run:
            args.append("--dry-run")
        return _run(args) or {}

    def order_by_client_id(self, client_order_id: str) -> Optional[Dict[str, Any]]:
        """Rule 9. After a timeout, ask before doing anything else.

        A request that timed out may well have succeeded. The only way to find
        out is to ask for it by the id we chose ourselves, which is why we
        always choose it ourselves.
        """
        try:
            return _run(["order", "get-by-client-id", "--client-order-id", client_order_id])
        except BrokerError:
            return None

    def cancel_all_orders(self) -> Any:
        return _run(["order", "cancel-all"])

    def close_all_positions(self) -> Any:
        """Sell everything. A 200 here means *accepted*, not *closed*.

        Probe 3 measured that directly: the response came back fine with the
        order still `pending_new` and nothing filled. So every caller of this
        verifies afterwards and calls again -- see flattener.py.
        """
        return _run(["position", "close-all", "--cancel-orders"], timeout=60)
