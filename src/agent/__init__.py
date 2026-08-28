"""The trading agent.

Nothing in this package is allowed to import anything outside Python's
standard library, with one exception: the drivers (`backtest`, `live`) and
`broker` may talk to the outside world. The strategy may not. A test in
`tests/test_strategy_imports.py` enforces that mechanically rather than
trusting anybody to remember it.
"""
