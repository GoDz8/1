"""Expected-value and probability modeling (spec §4 Step 4).

EV = Sum(p_i * payoff_i) - (spread_cost + fees), with probabilities from the
BS/IV-implied lognormal terminal distribution and payoff_i across a strike grid.
Also computes POP (probability of profit), expected return on capital, and
EV per dollar of max risk — the core gate quantities of spec §4.4.

The §2.5 rule is honored at the call site: pass the modeled slippage+fee cost
(from ``slippage.py``) so EV is reported NET of execution friction.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.stats import norm

from .payoff import Leg, economics, payoff_curve


@dataclass(frozen=True)
class EVResult:
    ev_gross: float            # EV before execution friction (dollars/contract)
    ev_after_slippage: float   # EV net of slippage + fees (dollars/contract)
    pop: float                 # probability of profit at expiry, in [0, 1]
    max_profit: float
    max_loss: float
    breakevens: tuple[float, ...]
    ev_per_dollar_risk: float  # ev_after_slippage / max_loss
    expected_return_on_capital: float  # ev_after_slippage / max_loss (alias view)


def terminal_distribution(
    spot: float, t: float, vol: float, rate: float, dividend: float = 0.0,
    n: int = 8001, width_sigmas: float = 6.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Discretized risk-neutral lognormal terminal-price distribution.

    Returns (price_grid, probability_weights) with weights summing to 1. Uses
    ln S_T ~ N(ln S0 + (r - q - 0.5 sigma^2) t, sigma^2 t).
    """
    if t <= 0 or vol <= 0 or spot <= 0:
        raise ValueError("terminal_distribution requires t>0, vol>0, spot>0")

    mu = math.log(spot) + (rate - dividend - 0.5 * vol ** 2) * t
    sd = vol * math.sqrt(t)
    lo = math.exp(mu - width_sigmas * sd)
    hi = math.exp(mu + width_sigmas * sd)
    grid = np.linspace(lo, hi, n)

    # Probability mass per grid cell via the lognormal CDF on cell edges.
    edges = np.empty(n + 1)
    edges[1:-1] = 0.5 * (grid[:-1] + grid[1:])
    edges[0] = max(grid[0] - (grid[1] - grid[0]) / 2.0, 1e-9)
    edges[-1] = grid[-1] + (grid[-1] - grid[-2]) / 2.0
    z = (np.log(edges) - mu) / sd
    cdf = norm.cdf(z)
    weights = np.diff(cdf)
    total = weights.sum()
    if total <= 0:
        raise ValueError("degenerate terminal distribution")
    return grid, weights / total


def expected_value(
    legs: list[Leg],
    spot: float,
    t: float,
    vol: float,
    rate: float,
    friction_cost: float = 0.0,
    dividend: float = 0.0,
) -> EVResult:
    """Full EV / POP / expectancy for a defined-risk structure.

    ``friction_cost`` is the total modeled slippage + fees per contract
    (dollars), subtracted from gross EV per spec §2.5.
    """
    grid, weights = terminal_distribution(spot, t, vol, rate, dividend)
    payoffs = payoff_curve(legs, grid)

    ev_gross = float(np.dot(weights, payoffs))
    ev_after = ev_gross - friction_cost
    pop = float(weights[payoffs > 0].sum())

    econ = economics(legs)
    max_loss = econ.max_loss
    epd = ev_after / max_loss if max_loss > 0 else 0.0

    return EVResult(
        ev_gross=round(ev_gross, 4),
        ev_after_slippage=round(ev_after, 4),
        pop=round(pop, 6),
        max_profit=econ.max_profit,
        max_loss=max_loss,
        breakevens=econ.breakevens,
        ev_per_dollar_risk=round(epd, 6),
        expected_return_on_capital=round(epd, 6),
    )
