"""Slippage / fill model tests."""

import pytest

from axiom.config import SlippageConfig
from axiom.quant.payoff import Leg, Right, Side
from axiom.quant.slippage import (
    Quote, leg_slippage_cost, modeled_fill_price, structure_friction,
)


def test_quote_helpers():
    q = Quote(bid=1.00, ask=1.20)
    assert q.mid == pytest.approx(1.10)
    assert q.width == pytest.approx(0.20)
    assert q.width_pct == pytest.approx(0.20 / 1.10)


def test_buy_fills_above_mid_sell_below():
    cfg = SlippageConfig(spread_fraction_paid=1.0)
    q = Quote(1.00, 1.20)
    assert modeled_fill_price(q, Side.BUY, cfg) == pytest.approx(1.20)   # full to ask
    assert modeled_fill_price(q, Side.SELL, cfg) == pytest.approx(1.00)  # full to bid


def test_mid_fill_when_fraction_zero():
    cfg = SlippageConfig(spread_fraction_paid=0.0)
    q = Quote(1.00, 1.20)
    assert modeled_fill_price(q, Side.BUY, cfg) == pytest.approx(1.10)


def test_leg_slippage_cost_dollars():
    cfg = SlippageConfig(spread_fraction_paid=1.0)
    q = Quote(1.00, 1.20)  # half-spread 0.10 -> $10/contract
    assert leg_slippage_cost(q, cfg) == pytest.approx(10.0)


def test_structure_friction_round_trip():
    cfg = SlippageConfig(spread_fraction_paid=1.0, per_contract_fee=0.0,
                         per_contract_exchange_fee=0.03)
    legs_quotes = [
        (Leg(Side.SELL, Right.PUT, 95, 2.0), Quote(1.90, 2.10)),  # half 0.10 -> $10
        (Leg(Side.BUY, Right.PUT, 90, 1.0), Quote(0.90, 1.10)),   # half 0.10 -> $10
    ]
    # one side slippage = $20, fees/side = 2 legs * 0.03 = 0.06; round trip x2
    friction = structure_friction(legs_quotes, cfg, round_trip=True)
    assert friction == pytest.approx((20.0 + 0.06) * 2, abs=1e-6)
