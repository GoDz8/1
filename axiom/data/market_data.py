"""Free supplementary market data via yfinance (spec §6).

Used for regime detection, realized-vol computation, and backtesting on
underlying prices. Fails closed (returns None) on any error so a data outage
degrades to PASS, never to a fabricated input.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from ..logging_setup import get_logger
from .base import with_retry

_log = get_logger("market_data")


@with_retry(attempts=3, base_delay=1.0)
def fetch_history(symbol: str, period: str = "1y", interval: str = "1d") -> pd.DataFrame | None:
    """Daily OHLCV history. Returns None on failure (handled by with_retry)."""
    import yfinance as yf  # imported lazily so offline tests need not hit it

    df = yf.Ticker(symbol).history(period=period, interval=interval)
    if df is None or df.empty:
        raise ValueError(f"no history for {symbol}")
    return df


def realized_vol(closes: "pd.Series | np.ndarray | list[float]", window: int = 20) -> float | None:
    """Annualized realized volatility from the last ``window`` daily closes.

    Pure function (no network) so it is unit-testable with synthetic series.
    """
    arr = np.asarray(list(closes), dtype=float)
    if len(arr) < window + 1:
        return None
    rets = np.diff(np.log(arr[-(window + 1):]))
    if len(rets) < 2:
        return None
    daily_sd = float(np.std(rets, ddof=1))
    return daily_sd * math.sqrt(252.0)


def simple_moving_average(closes: "pd.Series | np.ndarray | list[float]", window: int) -> float | None:
    arr = np.asarray(list(closes), dtype=float)
    if len(arr) < window:
        return None
    return float(np.mean(arr[-window:]))
