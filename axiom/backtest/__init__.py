"""Event-driven backtester (spec §7 Phase 1 item 6).

Validates each strategy's expectancy on real underlying data + MODELED option
pricing BEFORE it is allowed to trade live (spec §2.6). See the data-limitation
notes in ``engine.py``: with only underlying history we model option prices via
Black-Scholes using realized vol as an IV proxy, which understates the very
volatility-risk-premium edge AXIOM harvests — so backtest expectancy here is a
CONSERVATIVE lower bound, not a promise.
"""
