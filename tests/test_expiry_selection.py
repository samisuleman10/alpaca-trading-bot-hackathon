"""The expiry must be one that exists, not one the calendar suggests.

The original rule added a day and skipped weekends. On Friday 4 September 2026
that lands on Labor Day, when the market is shut and no contract in the United
States expires -- so the final session of the competition would have asked for
an empty chain on every signal and traded nothing.

These tests hold the fix in place with a fake broker, so they need no network.
"""
import unittest

from agent.live import Trader
from agent.params import Config


class FakeBroker(object):
    """Answers only the one question the expiry rule is allowed to ask."""

    def __init__(self, listed):
        self.listed = listed
        self.calls = 0

    def listed_expiries(self, on_or_after, through):
        self.calls += 1
        return [d for d in self.listed if on_or_after <= d <= through]


def trader(listed):
    broker = FakeBroker(listed)
    t = Trader.__new__(Trader)          # no network, no account probe
    t.config = Config()
    t.broker = broker
    t._expiry_cache = {}
    return t, broker


class ExpirySelection(unittest.TestCase):

    # The exact failure this file exists for.
    def test_labor_day_friday_does_not_ask_for_a_dead_date(self):
        listed = ["2026-09-08", "2026-09-09", "2026-09-10", "2026-09-11"]
        t, _ = trader(listed)
        chosen = t._nearest_expiry("2026-09-04")
        self.assertEqual(chosen, "2026-09-08")
        self.assertNotEqual(chosen, "2026-09-07")

    def test_ordinary_day_still_takes_tomorrow(self):
        t, _ = trader(["2026-09-03", "2026-09-04", "2026-09-08"])
        self.assertEqual(t._nearest_expiry("2026-09-02"), "2026-09-03")

    # A weekly-expiry fund like DIA lists Fridays only.
    def test_weekly_only_underlying_rolls_forward(self):
        t, _ = trader(["2026-09-04", "2026-09-11"])
        self.assertEqual(t._nearest_expiry("2026-09-08"), "2026-09-11")

    def test_nothing_listed_returns_none_rather_than_guessing(self):
        t, _ = trader([])
        self.assertIsNone(t._nearest_expiry("2026-09-04"))

    def test_answer_is_cached_for_the_session(self):
        t, broker = trader(["2026-09-03"])
        t._nearest_expiry("2026-09-02")
        t._nearest_expiry("2026-09-02")
        self.assertEqual(broker.calls, 1)


if __name__ == "__main__":
    unittest.main()
