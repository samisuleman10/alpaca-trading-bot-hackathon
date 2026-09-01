"""The record. Every minute the system was awake, whether it acted or not.

**Why the quiet minutes are written down too.** A trading log that contains
only trades tells you what happened and hides what was considered. Roughly
1,900 minutes a week this agent looks at the market and decides to do nothing,
and each of those is a decision with a reason behind it. Keeping them is what
turns "it traded eleven times" into "it looked 1,900 times, formed an opinion
on 34, could express 19 of those at an acceptable price, and was allowed to
take 11" -- which is the actual behaviour of the thing.

It is also the only way an outsider can check us. Every row carries the numbers
the rule read and the fingerprint of the settings in force, so somebody who
distrusts our conclusions can recompute the decision from the row and see
whether the system did what it claimed. That is the whole point, and it is why
the file is append-only: a record that can be edited after the fact proves
nothing about what was believed before it.

**Three streams**, matching the three questions anyone asks of a trading system:

- `decisions` -- what did you think, every minute, and why?
- `orders`    -- what did you actually send, and what came back?
- `sessions`  -- how did each day start and end?

They are written as JSON Lines: one self-contained JSON object per line,
appended and never rewritten. A crash mid-write costs at most the last line,
and every line before it stays readable -- which is not true of a single large
JSON document. The dashboard reads these files directly, so there is no
database standing between the record and the page showing it.
"""

from __future__ import annotations

import io
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

DECISIONS = "decisions"
ORDERS = "orders"
SESSIONS = "sessions"


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Journal:
    """Append-only writers for the three streams.

    Every write is opened, written, flushed and closed. That is slower than
    holding a handle open and it is the right trade: the process this serves is
    killed at the end of every trading day and may be killed unexpectedly in
    the middle of one, and a buffered line that never reached the disk is a
    decision that, as far as the record is concerned, was never made.
    """

    def __init__(self, root: str, session: str, params_hash: str, version: str) -> None:
        self.root = root
        self.session = session
        self.params_hash = params_hash
        self.version = version
        os.makedirs(root, exist_ok=True)

    def _path(self, stream: str) -> str:
        return os.path.join(self.root, "%s_%s.jsonl" % (self.session, stream))

    def write(self, stream: str, row: Dict[str, Any]) -> Dict[str, Any]:
        """Append one row, stamped with when and under what settings."""
        full = dict(row)
        full.setdefault("t_utc", _now_utc())
        full["session"] = self.session
        full["params_hash"] = self.params_hash
        full["version"] = self.version
        line = json.dumps(full, sort_keys=True, separators=(",", ":"), default=str)
        with io.open(self._path(stream), "a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
            handle.flush()
        return full

    # -- the three streams, named so a caller cannot pass the wrong string ---

    def decision(
        self,
        bar_t_utc: str,
        bar_t_et: str,
        close: float,
        action: str,
        reason: str,
        evidence: Optional[Dict[str, Any]] = None,
        **extra: Any
    ) -> Dict[str, Any]:
        """One minute, and what we made of it.

        `action` is the short machine-readable verdict -- `no_signal`,
        `declined`, `refused`, `shrunk`, `entered`, `exited`, `holding`,
        `skipped_no_bar`. `reason` is the same thing in a sentence a person can
        read. Both, always: the string is for the dashboard and the sentence is
        for whoever has to understand what this thing was doing at 10:47.
        """
        row = {
            "bar_t_utc": bar_t_utc,
            "bar_t_et": bar_t_et,
            "close": close,
            "action": action,
            "reason": reason,
            "evidence": evidence or {},
        }
        row.update(extra)
        return self.write(DECISIONS, row)

    def order(self, kind: str, client_order_id: str, request: Dict[str, Any],
              response: Optional[Dict[str, Any]], error: Optional[str] = None) -> Dict[str, Any]:
        """What we sent and what came back, including when it failed.

        The request is stored as well as the response. When an order does
        something we did not expect, the first question is always whether we
        sent what we thought we sent, and that question is unanswerable if only
        the reply was kept.
        """
        return self.write(ORDERS, {
            "kind": kind,
            "client_order_id": client_order_id,
            "request": request,
            "response": response,
            "error": error,
        })

    def session_event(self, event: str, detail: Dict[str, Any]) -> Dict[str, Any]:
        """How the day started, how it ended, and anything that stopped it."""
        return self.write(SESSIONS, {"event": event, "detail": detail})
