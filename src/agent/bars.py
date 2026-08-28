"""Price history, and the guard that stops us reading the future.

A **bar** is one minute of trading compressed into a few numbers: the price at
the start, the highest, the lowest, the price at the end, and how much changed
hands. It is the unit everything else is built on.

The dangerous thing about a backtest is that the whole year is sitting in
memory at once. Nothing physically prevents a rule from peeking at tomorrow's
price and looking brilliant. That mistake is called **look-ahead**, and it is
the single most common way a backtest lies to its author -- the third-party
system audited in the parent repository has exactly this bug, which is why none
of its results can be cited.

Being careful is not a defence. So the strategy is never handed the array. It
is handed a BarWindow: a reference to the whole array plus one number, the
index of the bar it is currently looking at. Reading at or before that index
works. Reading past it raises LookAheadError -- not a warning, not a lint rule,
an exception that stops the run. tests/test_lookahead_guard.py deliberately
reaches into the future and asserts that it blows up, so the guard is proven
rather than assumed.

The window also carries running totals, computed once when it is built. They
exist so a rule can ask "what is the average price everyone has paid today?"
without adding up several hundred bars on every single minute -- which would be
correct but far too slow to sweep. Every total is read strictly at or before
the current index, so the shortcut buys speed and gives up nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence


class LookAheadError(IndexError):
    """Raised when something tries to read a bar that has not happened yet."""


@dataclass(frozen=True)
class Bar:
    """One minute of trading.

    vwap is the average price paid during this minute, weighted by how much
    traded at each price. trades is how many separate transactions occurred.
    t_utc is the moment the minute ended, in UTC, which is what everything is
    stored and compared in. t_et is the same moment as a New York wall clock
    reading -- carried alongside rather than computed, so that a rule wanting
    to know whether it is past 15:40 can read it off directly instead of doing
    timezone arithmetic. session is the New York calendar date the minute
    belongs to, which is how we know where one trading day ends and the next
    begins -- never by subtracting hours from a timestamp, because of daylight
    saving.
    """

    t_utc: str
    t_et: str
    session: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    trades: int = 0
    vwap: float = 0.0


class BarWindow:
    """A view over price history, truncated at the current bar.

    Index it like a list. window[-1] is the bar that just closed, window[-2]
    the one before it. Positive indices count from the very first bar of the
    whole history. Either way, anything past the current bar raises.
    """

    __slots__ = ("_bars", "_index", "_cum_volume", "_cum_price_volume", "_session_start")

    def __init__(self, bars: Sequence[Bar], index: int) -> None:
        if not 0 <= index < len(bars):
            raise IndexError("index %d outside history of %d bars" % (index, len(bars)))
        self._bars = bars
        self._index = index
        self._cum_volume, self._cum_price_volume, self._session_start = _precompute(bars)

    # -- the driver advances the window one bar at a time -------------------

    def advanced_to(self, index: int) -> "BarWindow":
        """A window on the same history, one (or more) bars later.

        Reuses the running totals rather than recomputing them, because the
        driver builds one of these for every minute of the year.
        """
        if not 0 <= index < len(self._bars):
            raise IndexError("index %d outside history of %d bars" % (index, len(self._bars)))
        moved = object.__new__(BarWindow)
        moved._bars = self._bars
        moved._index = index
        moved._cum_volume = self._cum_volume
        moved._cum_price_volume = self._cum_price_volume
        moved._session_start = self._session_start
        return moved

    # -- reading ------------------------------------------------------------

    @property
    def index(self) -> int:
        """Where we are in the full history."""
        return self._index

    @property
    def current(self) -> Bar:
        """The bar that just closed. This is the newest thing we may look at."""
        return self._bars[self._index]

    def __len__(self) -> int:
        """How many bars are visible. Behaves like a list truncated at now."""
        return self._index + 1

    def __getitem__(self, key):
        visible = self._index + 1
        if isinstance(key, slice):
            # Standard slice semantics against the visible portion only, so a
            # slice can never reach past now however it is written.
            return [self._bars[i] for i in range(*key.indices(visible))]
        position = key + visible if key < 0 else key
        if position > self._index:
            raise LookAheadError(
                "tried to read bar %d while standing on bar %d -- that bar has not happened yet"
                % (position, self._index)
            )
        if position < 0:
            raise IndexError("bar %d is before the start of the history" % position)
        return self._bars[position]

    def __iter__(self):
        for i in range(self._index + 1):
            yield self._bars[i]

    # -- running totals -----------------------------------------------------

    @property
    def session_start(self) -> int:
        """Index of the first bar of the trading day the current bar sits in."""
        return self._session_start[self._index]

    @property
    def minutes_into_session(self) -> int:
        """How many bars have closed today, counting the current one."""
        return self._index - self.session_start + 1

    def _check(self, start: int, stop: int) -> None:
        if stop > self._index + 1:
            raise LookAheadError(
                "tried to total bars up to %d while standing on bar %d" % (stop - 1, self._index)
            )
        if start < 0:
            raise IndexError("bar %d is before the start of the history" % start)

    def sum_volume(self, start: int, stop: int) -> float:
        """Total volume over bars start .. stop - 1."""
        self._check(start, stop)
        return self._cum_volume[stop] - self._cum_volume[start]

    def sum_price_volume(self, start: int, stop: int) -> float:
        """Total of price times volume over bars start .. stop - 1."""
        self._check(start, stop)
        return self._cum_price_volume[stop] - self._cum_price_volume[start]

    def session_vwap(self) -> Optional[float]:
        """The average price everyone has paid so far today, weighted by size.

        Called VWAP -- volume-weighted average price. A share trading below it
        is cheap relative to what the day's buyers have paid on average.
        Returns None if nothing has traded yet today, rather than dividing by
        zero.
        """
        start, stop = self.session_start, self._index + 1
        volume = self.sum_volume(start, stop)
        if volume <= 0:
            return None
        return self.sum_price_volume(start, stop) / volume

    def mean_volume(self, window: int) -> Optional[float]:
        """Average volume over the `window` bars *before* the current one.

        The current bar is excluded on purpose. Including it would make any
        "is this minute busier than usual?" test partly a comparison of the
        minute against itself, which drags the answer toward 1 and hides the
        signal it is supposed to find.

        Returns None until there are enough bars in today's session, and never
        quietly shortens the window -- a minute with no trades produces no bar
        at all, so a window that silently shrinks is measuring a different span
        of time than it claims to.
        """
        stop = self._index
        start = stop - window
        if window <= 0 or start < self.session_start:
            return None
        return self.sum_volume(start, stop) / window


def _precompute(bars: Sequence[Bar]):
    """Running totals and session starts, computed once for the whole history.

    Building these over the entire array is not look-ahead: every read above is
    clamped to the current index. It is the difference between having a ruler
    in the room and using it to measure something that has not arrived yet.
    """
    n = len(bars)
    cum_volume: List[float] = [0.0] * (n + 1)
    cum_price_volume: List[float] = [0.0] * (n + 1)
    session_start: List[int] = [0] * n
    previous_session = None
    start = 0
    for i, bar in enumerate(bars):
        if bar.session != previous_session:
            start = i
            previous_session = bar.session
        session_start[i] = start
        # Use the minute's own average traded price where we have it, and fall
        # back to its closing price where we do not. Which one is used is a
        # recorded decision, not a detail -- see docs/strategy_candidates.md.
        price = bar.vwap if bar.vwap > 0 else bar.close
        cum_volume[i + 1] = cum_volume[i] + bar.volume
        cum_price_volume[i + 1] = cum_price_volume[i] + price * bar.volume
    return cum_volume, cum_price_volume, session_start
