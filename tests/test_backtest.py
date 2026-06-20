"""Backtester tests on synthetic price series (no network)."""

import numpy as np

from axiom.config import Config
from axiom.backtest.engine import backtest_put_credit_spread

CFG = Config()


def _gbm(n=400, mu=0.08, sigma=0.18, s0=100.0, seed=7):
    rng = np.random.default_rng(seed)
    dt = 1 / 252
    rets = (mu - 0.5 * sigma**2) * dt + sigma * np.sqrt(dt) * rng.standard_normal(n)
    return s0 * np.exp(np.cumsum(rets))


def test_backtest_runs_and_reports():
    res = backtest_put_credit_spread(_gbm(), CFG, dte=30)
    assert res.n_trades > 0
    assert 0.0 <= res.win_rate <= 1.0


def test_backtest_handles_short_series():
    res = backtest_put_credit_spread([100.0] * 10, CFG, dte=30)
    assert res.n_trades == 0
    assert res.avg_pnl == 0.0


def test_zero_vol_series_produces_no_trades():
    # A perfectly smooth series has zero realized vol -> IV proxy 0 -> no entries
    # (fail closed; we never price an option off a zero/unknown vol).
    prices = [100.0 * (1.0003 ** i) for i in range(400)]
    res = backtest_put_credit_spread(prices, CFG, dte=30)
    assert res.n_trades == 0


def test_uptrend_with_noise_trades_and_mostly_wins():
    # Noisy uptrend: OTM put credit spreads should be tested but mostly expire OK.
    res = backtest_put_credit_spread(_gbm(mu=0.12, sigma=0.15, seed=3), CFG, dte=30)
    assert res.n_trades > 0
    assert res.win_rate >= 0.5
