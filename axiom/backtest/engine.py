"""Event-driven backtester for defined-risk premium strategies.

DATA LIMITATIONS (read before trusting any number this produces):
  * We have underlying price history, NOT a historical option-quote tape. Option
    entry prices are MODELED with Black-Scholes using trailing realized vol as
    the IV input. Because implied vol persistently exceeds realized (the VRP that
    is AXIOM's core edge, §2.1), pricing entries at realized vol UNDERSTATES the
    premium a seller actually collects. Results here are therefore a conservative
    lower bound on a premium-selling strategy's true expectancy.
  * Exits are modeled at expiry intrinsic value (no early management), so the
    'manage winners at 50%' hypotheses (§5.5) are NOT what is tested here.
  * No lookahead: vol/MA at each entry uses only data up to that bar (§11 meta).

Use this to REJECT clearly negative-expectancy strategies, not to certify a
precise edge. Phase 2 replaces realized-vol pricing with paper-fill calibration.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..config import Config
from ..data.market_data import realized_vol
from ..quant.black_scholes import BSInputs, price
from ..quant.greeks import delta as bs_delta
from ..quant.payoff import Leg, Right, Side, economics, payoff_curve


@dataclass(frozen=True)
class BacktestResult:
    strategy: str
    symbol: str
    n_trades: int
    win_rate: float
    avg_pnl: float            # dollars per contract per trade
    total_pnl: float
    expectancy_per_risk: float  # avg_pnl / avg_max_loss
    sharpe_like: float        # mean/std of per-trade pnl (not annualized)

    def is_positive_expectancy(self) -> bool:
        return self.avg_pnl > 0


def _select_strike(spot: float, t: float, rate: float, iv: float,
                   right: str, target_delta: float) -> float:
    """Find the strike whose |delta| ~ target on a fine strike grid."""
    is_call = right == "CALL"
    grid = np.linspace(spot * 0.5, spot * 1.5, 401)
    best, best_err = spot, 1e9
    for k in grid:
        d = abs(bs_delta(BSInputs(spot, float(k), t, rate, iv), is_call))
        if abs(d - target_delta) < best_err:
            best, best_err = float(k), abs(d - target_delta)
    return round(best, 2)


def backtest_put_credit_spread(
    closes: list[float] | np.ndarray, cfg: Config, dte: int = 30,
    vol_window: int = 20, short_delta: float = 0.30, long_delta: float = 0.20,
) -> BacktestResult:
    """Roll a 30-DTE put credit spread; hold to expiry; aggregate expectancy."""
    arr = np.asarray(list(closes), dtype=float)
    rate = cfg.risk_free_rate
    t = dte / 365.0
    pnls: list[float] = []
    max_losses: list[float] = []

    i = vol_window
    while i + dte < len(arr):
        window = arr[: i + 1]
        iv = realized_vol(window, vol_window)
        if iv is None or iv <= 0:
            i += dte
            continue
        spot = float(arr[i])
        ks = _select_strike(spot, t, rate, iv, "PUT", short_delta)
        kl = _select_strike(spot, t, rate, iv, "PUT", long_delta)
        if ks <= kl:
            i += dte
            continue
        # Model entry premiums at realized-vol BS (conservative, see header).
        short_prem = price(BSInputs(spot, ks, t, rate, iv), is_call=False)
        long_prem = price(BSInputs(spot, kl, t, rate, iv), is_call=False)
        legs = [
            Leg(Side.SELL, Right.PUT, ks, short_prem),
            Leg(Side.BUY, Right.PUT, kl, long_prem),
        ]
        terminal = float(arr[i + dte])
        pnl = float(payoff_curve(legs, np.array([terminal]))[0])
        pnls.append(pnl)
        max_losses.append(economics(legs).max_loss)
        i += dte

    if not pnls:
        return BacktestResult("put_credit_spread", "", 0, 0.0, 0.0, 0.0, 0.0, 0.0)

    pnls_arr = np.array(pnls)
    wins = float(np.mean(pnls_arr > 0))
    avg = float(np.mean(pnls_arr))
    std = float(np.std(pnls_arr, ddof=1)) if len(pnls_arr) > 1 else 0.0
    avg_ml = float(np.mean(max_losses))
    return BacktestResult(
        strategy="put_credit_spread", symbol="",
        n_trades=len(pnls), win_rate=round(wins, 4),
        avg_pnl=round(avg, 2), total_pnl=round(float(np.sum(pnls_arr)), 2),
        expectancy_per_risk=round(avg / avg_ml, 4) if avg_ml > 0 else 0.0,
        sharpe_like=round(avg / std, 4) if std > 0 else 0.0,
    )
