"""The registry of trading rules.

A strategy is one function with a fixed signature. The trader, the risk layer,
the journal and the dashboard never learn its name -- switching strategies is a
configuration change, not a code change.
"""

from __future__ import annotations

from typing import Callable, Dict

from . import vwap_reversion

# name -> decide function.  Add a strategy by adding a line here.
REGISTRY: Dict[str, Callable] = {
    "vwap_reversion": vwap_reversion.decide,
}


def get(name: str) -> Callable:
    """Look up a strategy by name, failing loudly rather than silently."""
    if name not in REGISTRY:
        known = ", ".join(sorted(REGISTRY)) or "(none)"
        raise KeyError("unknown strategy %r; known strategies: %s" % (name, known))
    return REGISTRY[name]
