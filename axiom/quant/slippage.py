"""Slippage / realistic-fill model (spec §2.5).

Every EV calculation subtracts the full bid-ask spread (mid-to-natural, not
mid) plus fees. This module models the per-contract execution friction and the
modeled fill price for a leg, so a 'good-looking' mid-priced trade that is
negative-EV after spread is correctly rejected.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import SlippageConfig
from .payoff import CONTRACT_MULTIPLIER, Leg, Side


@dataclass(frozen=True)
class Quote:
    bid: float
    ask: float

    @property
    def mid(self) -> float:
        return 0.5 * (self.bid + self.ask)

    @property
    def width(self) -> float:
        return self.ask - self.bid

    @property
    def width_pct(self) -> float:
        return self.width / self.mid if self.mid > 0 else float("inf")


def modeled_fill_price(quote: Quote, side: Side, cfg: SlippageConfig) -> float:
    """Modeled fill for a single leg, worse than mid by a fraction of the spread.

    A BUY fills above mid (toward the ask); a SELL fills below mid (toward the
    bid). ``spread_fraction_paid`` of the half-spread is given up on each leg.
    """
    half = 0.5 * quote.width * cfg.spread_fraction_paid
    return quote.mid + half if side is Side.BUY else quote.mid - half


def leg_slippage_cost(quote: Quote, cfg: SlippageConfig) -> float:
    """Per-contract slippage cost (dollars) for one leg vs a mid fill."""
    half = 0.5 * quote.width * cfg.spread_fraction_paid
    return half * CONTRACT_MULTIPLIER


def structure_friction(
    legs_quotes: list[tuple[Leg, Quote]], cfg: SlippageConfig,
    round_trip: bool = True,
) -> float:
    """Total modeled friction per contract (dollars): slippage + fees.

    ``round_trip`` accounts for both entry and exit slippage + fees, which is
    the honest cost of a position that must eventually be closed.
    """
    n_legs = len(legs_quotes)
    # Slippage given up across all legs on one side (dollars/contract).
    slip_per_side = sum(leg_slippage_cost(q, cfg) for _, q in legs_quotes)
    # Fees are per-contract, per-leg, per-side (dollars/contract).
    fees_per_side = n_legs * (cfg.per_contract_fee + cfg.per_contract_exchange_fee)
    sides = 2 if round_trip else 1
    return (slip_per_side + fees_per_side) * sides
