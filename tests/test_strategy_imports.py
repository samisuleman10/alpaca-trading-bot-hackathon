"""The strategy is not allowed to touch the outside world.

The whole design rests on one promise: the same function makes the decision in
the backtest and in the live trader, so the rehearsal and the real thing cannot
drift apart. That promise dies the moment a strategy reaches for the network,
the clock, a file, or the account balance -- because then it is no longer being
handed the same inputs in both places.

Rather than trust ourselves to remember, this test reads the strategy files as
text, parses them, and lists every module they import. Anything outside Python's
standard library, and outside the handful of our own modules that are themselves
pure, fails the build.

Why parse instead of just importing and checking? Because an import that runs
has already done whatever it was going to do. Reading the source finds the
problem before it executes.
"""

from __future__ import annotations

import ast
import os

import pytest

SRC = os.path.join(os.path.dirname(__file__), "..", "src")
STRATEGIES = os.path.join(SRC, "agent", "strategies")

# Our own modules a strategy may import. Each is data-and-arithmetic only and
# is itself covered by this test, so the purity claim is not circular.
OWN_PURE_MODULES = {"bars", "contracts", "params"}

# Sibling strategy modules the registry pulls in. Each is itself put through
# this test, so allowing them here is not a loophole.
SIBLING_STRATEGIES = {"vwap_reversion"}

# Standard-library modules a strategy has any business wanting. Deliberately
# short. `datetime` is absent on purpose: a strategy that asks what time it is
# gets a different answer in the backtest than it does live, which is exactly
# the divergence this file exists to prevent. Times arrive on the bar.
ALLOWED_STDLIB = {
    "__future__",
    "math",
    "statistics",
    "typing",
    "dataclasses",
    "collections",
    "itertools",
    "functools",
    "enum",
}


def strategy_files():
    return sorted(
        os.path.join(STRATEGIES, name)
        for name in os.listdir(STRATEGIES)
        if name.endswith(".py")
    )


def imports_of(path):
    """Every module name imported by a file, as (module, line) pairs.

    A relative import like `from ..bars import BarWindow` is reported as
    "bars" -- the leading dots are how it reaches our own package, and what we
    care about is which module it lands on.
    """
    with open(path, "r", encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=path)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((alias.name.split(".")[0], node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative: ".", "..", inside our own package
                if node.module:
                    found.append((node.module.split(".")[0], node.lineno))
                else:
                    # `from . import vwap_reversion` -- pulling in a sibling
                    # strategy module. Legitimate, and each sibling is put
                    # through this same test, so nothing slips through.
                    for alias in node.names:
                        found.append((alias.name.split(".")[0], node.lineno))
            else:
                found.append(((node.module or "").split(".")[0], node.lineno))
    return found


def test_there_is_at_least_one_strategy():
    """Guard against this test passing because it checked nothing."""
    files = [f for f in strategy_files() if not f.endswith("__init__.py")]
    assert files, "no strategy files found -- this test would pass vacuously"


@pytest.mark.parametrize("path", strategy_files(), ids=os.path.basename)
def test_strategy_imports_nothing_impure(path):
    for module, line in imports_of(path):
        if module in OWN_PURE_MODULES or module in ALLOWED_STDLIB:
            continue
        if module in SIBLING_STRATEGIES:
            continue
        raise AssertionError(
            "%s line %d imports %r.\n"
            "A strategy may import only %s from our own code, and only these "
            "standard-library modules: %s.\n"
            "If this import is genuinely needed, the work belongs in a driver "
            "(backtest.py or live.py), not in the rule."
            % (
                os.path.basename(path),
                line,
                module,
                ", ".join(sorted(OWN_PURE_MODULES)),
                ", ".join(sorted(ALLOWED_STDLIB)),
            )
        )


@pytest.mark.parametrize("name", sorted(OWN_PURE_MODULES))
def test_the_modules_a_strategy_may_import_are_themselves_pure(name):
    """Otherwise the rule above is a fence with a gate left open."""
    path = os.path.join(SRC, "agent", "%s.py" % name)
    for module, line in imports_of(path):
        if module in OWN_PURE_MODULES or module in ALLOWED_STDLIB:
            continue
        # params.py hashes its own contents for the journal; that is arithmetic
        # over data already in hand, not a reach outside.
        if name == "params" and module in {"hashlib", "json"}:
            continue
        raise AssertionError(
            "%s.py line %d imports %r, so it cannot be offered to strategies "
            "as a pure module." % (name, line, module)
        )
