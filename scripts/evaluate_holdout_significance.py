"""Apply v1.3's committed decision rule to any set of committed runs.

The rule (``strategies/strategy_04/v1_3/strategy.md``, committed 30 July 2026
before any holdout run existed):

- **Accept** when out-of-sample average R is positive with |t| clearing
  Student's two-sided 95% critical value at that run's own degrees of freedom.
- **Abandon** when average R is negative and |t| clears the same bar.
- Neither firing is itself an outcome, and every row must state its trade
  count so an underpowered result cannot read as a conclusion.

`HOLDOUT_RESULT.md` evaluated FX by this rule with arithmetic that was never
committed as code, so it could not be re-run when three more instruments
arrived. This script is that arithmetic.

It reads nothing but ``fixed_trades.csv`` and imports nothing from
``ai_trade``: the same independence the audit rules keep. A significance test
that called the strategy to obtain its own inputs would be measuring the
implementation's opinion of itself.

Usage::

    python scripts/evaluate_holdout_significance.py \
        --results strategies/strategy_04/v1_2/results \
        --symbols IWM GLD SLV
"""

from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

# The power calculation's target power (80%) and the rule's two-sided 95%
# confidence, as z-scores. Used only to report what a sample *could* have
# detected -- never to decide anything.
_Z_ALPHA = 1.959963984540054
_Z_POWER = 0.8416212335729143


def _log_beta(a: float, b: float) -> float:
    return math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the incomplete beta function (Lentz's method)."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 3.0e-16:
            break
    return h


def regularized_incomplete_beta(a: float, b: float, x: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(a * math.log(x) + b * math.log(1.0 - x) - _log_beta(a, b))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - math.exp(
        b * math.log(1.0 - x) + a * math.log(x) - _log_beta(b, a)
    ) * _betacf(b, a, 1.0 - x) / b


def t_sf(t: float, df: int) -> float:
    """Two-sided survival probability P(|T| > |t|) for Student's t."""
    x = df / (df + t * t)
    return regularized_incomplete_beta(df / 2.0, 0.5, x)


def t_critical_95(df: int) -> float:
    """Two-sided 95% critical value, by bisection on the survival function.

    The rule is explicit that |t| is compared against Student's t at the run's
    own degrees of freedom, not a flat 2.0 -- a flat 2.0 once marked a
    four-trade run conclusive.
    """
    low, high = 0.0, 1000.0
    for _ in range(200):
        mid = (low + high) / 2.0
        if t_sf(mid, df) > 0.05:
            low = mid
        else:
            high = mid
    return (low + high) / 2.0


def read_r_multiples(trades_csv: Path) -> List[float]:
    with trades_csv.open(newline="", encoding="utf-8") as handle:
        return [float(row["result_r"]) for row in csv.DictReader(handle)]


def evaluate(values: Sequence[float]) -> Optional[dict]:
    """Average R, t, the bar it must clear, and which rule fires."""
    n = len(values)
    if n < 2:
        # One trade has no dispersion estimate; t is undefined rather than
        # infinite, and reporting it as a verdict is how tiny samples get
        # mistaken for findings.
        return None
    mean = sum(values) / n
    variance = sum((value - mean) ** 2 for value in values) / (n - 1)
    sd = math.sqrt(variance)
    df = n - 1
    critical = t_critical_95(df)
    t = mean / (sd / math.sqrt(n)) if sd > 0 else 0.0
    if abs(t) >= critical:
        rule = "accept" if mean > 0 else "abandon"
    else:
        rule = "neither"
    return {
        "trades": n,
        "average_r": mean,
        "sd": sd,
        "t": t,
        "critical": critical,
        "rule": rule,
        # The smallest true edge this sample size could have resolved at 95%
        # confidence and 80% power. A result smaller than this says nothing.
        "detectable_edge": (_Z_ALPHA + _Z_POWER) * sd / math.sqrt(n),
    }


def collect(results_root: Path, symbols: Sequence[str], variants: Sequence[str]) -> List[Tuple[str, str, dict]]:
    rows: List[Tuple[str, str, dict]] = []
    for symbol in symbols:
        for variant in variants:
            trades_csv = results_root / f"{symbol.lower()}_1h_15m_{variant}" / "fixed_trades.csv"
            if not trades_csv.exists():
                continue
            verdict = evaluate(read_r_multiples(trades_csv))
            if verdict is not None:
                rows.append((symbol, variant, verdict))
    return rows


def render(rows: Sequence[Tuple[str, str, dict]]) -> str:
    lines = [
        "| Configuration | Symbol | Trades | Average R | t | Bar | Detectable edge | Rule |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    # Most negative first, matching HOLDOUT_RESULT.md's ordering: the abandon
    # rule is the one that has ever fired, so it leads.
    for symbol, variant, v in sorted(rows, key=lambda row: row[2]["t"]):
        verdict = v["rule"] if v["rule"] == "neither" else f"**{v['rule']}**"
        lines.append(
            f"| {variant} | {symbol} | {v['trades']} | {v['average_r']:+.4f} "
            f"| {v['t']:+.2f} | {v['critical']:.3f} | ±{v['detectable_edge']:.3f} | {verdict} |"
        )
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--symbols", nargs="+", required=True)
    parser.add_argument("--variants", nargs="+", default=("base", "a", "b", "ab"))
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write the table here as UTF-8. The console on Windows is cp1252 "
             "and mangles the ± in the detectable-edge column, so a table "
             "destined for a committed report should be written, not piped.",
    )
    args = parser.parse_args(argv)
    rows = collect(args.results, args.symbols, args.variants)
    if not rows:
        print("No runs found; nothing evaluated.")
        return 1
    table = render(rows)
    if args.output is not None:
        args.output.write_text(table + "\n", encoding="utf-8")
        print(f"Wrote {args.output}")
    print(table.encode("ascii", "replace").decode("ascii"))
    fired = [row for row in rows if row[2]["rule"] != "neither"]
    print()
    print(f"{len(fired)} of {len(rows)} runs fire a rule.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
