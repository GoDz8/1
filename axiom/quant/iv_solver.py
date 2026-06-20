"""Implied-volatility solver (spec §6: compute IV from quotes we already pull).

Brent's method on [vol_lo, vol_hi] with a no-arbitrage price check, falling
back to returning None (-> "unknown", spec honesty rule §6) when the observed
price is outside the arbitrage bounds rather than fabricating a number.

``tests/test_iv_solver.py`` verifies price -> IV -> price round-trips.
"""

from __future__ import annotations

import math

from scipy.optimize import brentq

from .black_scholes import BSInputs, price

VOL_LO = 1e-4
VOL_HI = 5.0  # 500% annualized — generous upper bound


def _no_arb_bounds(spot: float, strike: float, t: float, rate: float,
                   dividend: float, is_call: bool) -> tuple[float, float]:
    """Return (lower, upper) no-arbitrage price bounds for the option."""
    disc_s = spot * math.exp(-dividend * t)
    disc_k = strike * math.exp(-rate * t)
    if is_call:
        return max(disc_s - disc_k, 0.0), disc_s
    return max(disc_k - disc_s, 0.0), disc_k


def implied_vol(
    market_price: float,
    spot: float,
    strike: float,
    t: float,
    rate: float,
    is_call: bool,
    dividend: float = 0.0,
) -> float | None:
    """Solve for IV. Returns None if the price is un-invertible (no fabrication)."""
    if market_price <= 0 or t <= 0 or spot <= 0 or strike <= 0:
        return None

    lo_px, hi_px = _no_arb_bounds(spot, strike, t, rate, dividend, is_call)
    # Allow a tiny tolerance for rounding at the bounds.
    tol = 1e-6
    if market_price < lo_px - tol or market_price > hi_px + tol:
        return None  # outside arbitrage bounds -> unknown

    def objective(vol: float) -> float:
        return price(BSInputs(spot, strike, t, rate, vol, dividend), is_call) - market_price

    f_lo = objective(VOL_LO)
    f_hi = objective(VOL_HI)
    if f_lo * f_hi > 0:
        # No sign change in the bracket; can't bracket a root reliably.
        return None
    try:
        return float(brentq(objective, VOL_LO, VOL_HI, xtol=1e-8, maxiter=200))
    except (ValueError, RuntimeError):
        return None
