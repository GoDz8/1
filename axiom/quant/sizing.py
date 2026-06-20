"""Position sizing: fractional Kelly, conviction-scaled, hard-capped (spec §5.1).

Sizing pipeline (no double-counting of edge — spec §5.1):
1. Raw Kelly f* from the modeled edge/odds (p, b).
2. quarter_kelly = 0.25 * f*  (full Kelly is ruinous on estimation error).
3. Conviction acts ONLY as a fractional multiplier on quarter-Kelly for the
   softer factors — it never sizes ABOVE quarter-Kelly.
4. capital-at-risk = min(conviction-scaled quarter-Kelly * NLV, per-trade cap),
   then bounded by remaining portfolio heat.
5. contracts = floor(allowed capital / per-contract max loss).

Records both ``kelly_fraction`` (raw f*) and ``applied_fraction`` so every
sizing decision is reconstructable (spec §5.1 / §8).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..config import SizingConfig


@dataclass(frozen=True)
class SizingResult:
    kelly_fraction: float       # raw f*
    applied_fraction: float     # after conviction scalar + caps (of NLV)
    contracts: int
    capital_at_risk: float      # dollars
    pct_of_nlv: float
    binding_constraint: str     # which limit set the size: kelly|cap|heat|none


def kelly_fraction(pop: float, max_profit: float, max_loss: float) -> float:
    """Raw Kelly f* = (b*p - q) / b, clamped at 0. b = max_profit/max_loss."""
    if max_loss <= 0 or max_profit <= 0:
        return 0.0
    b = max_profit / max_loss
    p = max(0.0, min(1.0, pop))
    q = 1.0 - p
    f = (b * p - q) / b
    return max(0.0, f)


def conviction_scalar(conviction: int, cfg: SizingConfig) -> float:
    """Map conviction [floor, high] -> [low_scalar, 1.0]; below floor -> 0."""
    if conviction < cfg.conviction_floor:
        return 0.0
    if conviction >= cfg.high_conviction:
        return 1.0
    span = cfg.high_conviction - cfg.conviction_floor
    frac = (conviction - cfg.conviction_floor) / span
    return cfg.conviction_low_scalar + frac * (1.0 - cfg.conviction_low_scalar)


def size_position(
    pop: float,
    max_profit: float,
    max_loss: float,
    conviction: int,
    nlv: float,
    per_trade_cap: float,
    remaining_heat_dollars: float,
    cfg: SizingConfig,
) -> SizingResult:
    """Compute contracts and capital-at-risk under all sizing constraints."""
    f_star = kelly_fraction(pop, max_profit, max_loss)
    quarter = cfg.kelly_fraction_multiplier * f_star
    scaled = quarter * conviction_scalar(conviction, cfg)

    if scaled <= 0 or nlv <= 0 or max_loss <= 0:
        return SizingResult(round(f_star, 6), 0.0, 0, 0.0, 0.0, "kelly")

    kelly_dollars = scaled * nlv
    cap_dollars = per_trade_cap * nlv
    heat_dollars = max(0.0, remaining_heat_dollars)

    allowed = min(kelly_dollars, cap_dollars, heat_dollars)
    contracts = int(math.floor(allowed / max_loss))
    capital_at_risk = contracts * max_loss

    if contracts == 0:
        binding = "heat" if heat_dollars < max_loss else (
            "cap" if cap_dollars < max_loss else "kelly")
        return SizingResult(round(f_star, 6), round(scaled, 6), 0, 0.0, 0.0, binding)

    # Identify the binding constraint for the audit trail.
    binding = "kelly"
    if allowed == cap_dollars and cap_dollars <= kelly_dollars and cap_dollars <= heat_dollars:
        binding = "cap"
    elif allowed == heat_dollars and heat_dollars <= kelly_dollars and heat_dollars <= cap_dollars:
        binding = "heat"

    return SizingResult(
        kelly_fraction=round(f_star, 6),
        applied_fraction=round(capital_at_risk / nlv, 6),
        contracts=contracts,
        capital_at_risk=round(capital_at_risk, 2),
        pct_of_nlv=round(capital_at_risk / nlv, 6),
        binding_constraint=binding,
    )
