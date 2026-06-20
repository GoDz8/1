"""Option payoff modeling for the structures the §8 schema can emit.

Supported (all defined-risk, per spec §2.1): put/call credit spreads, put/call
debit spreads, and iron condors. Naked long single options are deliberately
NOT a core structure (spec §2.2).

Payoffs are PER SHARE; multiply by 100 * contracts for dollars. Premiums are
positive numbers (price per share); ``side`` determines the sign of cash flow.
``tests/test_payoff.py`` pins hand-computed credit/debit/condor cases.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np

CONTRACT_MULTIPLIER = 100


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class Right(str, Enum):
    CALL = "CALL"
    PUT = "PUT"


class StructureType(str, Enum):
    PUT_CREDIT_SPREAD = "PUT_CREDIT_SPREAD"
    CALL_CREDIT_SPREAD = "CALL_CREDIT_SPREAD"
    IRON_CONDOR = "IRON_CONDOR"
    PUT_DEBIT_SPREAD = "PUT_DEBIT_SPREAD"
    CALL_DEBIT_SPREAD = "CALL_DEBIT_SPREAD"


@dataclass(frozen=True)
class Leg:
    side: Side
    right: Right
    strike: float
    premium: float  # per-share option price (mid or modeled fill), positive


@dataclass(frozen=True)
class StructureEconomics:
    """Per-contract economics of a defined-risk structure (in dollars)."""

    net_credit: float       # >0 credit received, <0 debit paid (per contract)
    max_profit: float       # dollars per contract
    max_loss: float         # dollars per contract (positive number)
    breakevens: tuple[float, ...]
    is_credit: bool


def _leg_intrinsic(leg: Leg, spot: np.ndarray) -> np.ndarray:
    """Intrinsic value of the option at expiry across a spot grid (per share)."""
    if leg.right is Right.CALL:
        return np.maximum(spot - leg.strike, 0.0)
    return np.maximum(leg.strike - spot, 0.0)


def payoff_curve(legs: list[Leg], spot_grid: np.ndarray) -> np.ndarray:
    """Total P&L per contract (dollars) across ``spot_grid`` at expiry."""
    total = np.zeros_like(spot_grid, dtype=float)
    for leg in legs:
        intrinsic = _leg_intrinsic(leg, spot_grid)
        if leg.side is Side.BUY:
            # paid premium up front, receive intrinsic at expiry
            total += intrinsic - leg.premium
        else:  # SELL
            total += leg.premium - intrinsic
    return total * CONTRACT_MULTIPLIER


def net_credit(legs: list[Leg]) -> float:
    """Net cash flow at entry per contract (dollars): credit>0, debit<0."""
    cash = 0.0
    for leg in legs:
        if leg.side is Side.SELL:
            cash += leg.premium
        else:
            cash -= leg.premium
    return cash * CONTRACT_MULTIPLIER


def _all_strikes(legs: list[Leg]) -> list[float]:
    return sorted({leg.strike for leg in legs})


def economics(legs: list[Leg]) -> StructureEconomics:
    """Compute defined-risk economics from the legs.

    Evaluates the payoff on a dense grid spanning well beyond the strikes; for
    vertical/condor structures the payoff is piecewise-linear with breaks only
    at strikes, so the extrema are exact at the grid resolution.
    """
    if not legs:
        raise ValueError("structure must have at least one leg")

    strikes = _all_strikes(legs)
    lo = max(0.01, strikes[0] * 0.5)
    hi = strikes[-1] * 1.5
    grid = np.linspace(lo, hi, 20_001)
    curve = payoff_curve(legs, grid)

    max_profit = float(np.max(curve))
    max_loss = float(-np.min(curve))  # positive magnitude of worst loss
    credit = net_credit(legs)

    # Breakevens: sign changes of the payoff curve (linear interpolation).
    # A grid point landing exactly on zero registers two adjacent sign changes
    # (neg->0, 0->pos); dedupe near-equal roots to one breakeven.
    raw: list[float] = []
    sign = np.sign(curve)
    idx = np.where(np.diff(sign) != 0)[0]
    for i in idx:
        x0, x1 = grid[i], grid[i + 1]
        y0, y1 = curve[i], curve[i + 1]
        if y1 != y0:
            raw.append(round(x0 - y0 * (x1 - x0) / (y1 - y0), 4))
    step = grid[1] - grid[0]
    breakevens: list[float] = []
    for be in sorted(raw):
        if not breakevens or abs(be - breakevens[-1]) > 2 * step:
            breakevens.append(be)

    return StructureEconomics(
        net_credit=round(credit, 4),
        max_profit=round(max_profit, 4),
        max_loss=round(max_loss, 4),
        breakevens=tuple(breakevens),
        is_credit=credit > 0,
    )
