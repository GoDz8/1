"""Sizing tests: Kelly math, conviction scaling, and the hard caps (spec §5.1)."""

import pytest

from axiom.config import SizingConfig
from axiom.quant.sizing import (
    conviction_scalar, kelly_fraction, size_position,
)

CFG = SizingConfig()


def test_kelly_formula():
    # b=1 (max_profit==max_loss), p=0.6 -> f* = (1*0.6 - 0.4)/1 = 0.2
    assert kelly_fraction(0.6, 100, 100) == pytest.approx(0.2)


def test_kelly_clamped_at_zero_for_negative_edge():
    # p=0.4, b=1 -> negative edge -> clamp to 0
    assert kelly_fraction(0.4, 100, 100) == 0.0


def test_conviction_scalar_bands():
    assert conviction_scalar(69, CFG) == 0.0            # below floor
    assert conviction_scalar(70, CFG) == pytest.approx(CFG.conviction_low_scalar)
    assert conviction_scalar(85, CFG) == pytest.approx(1.0)
    assert conviction_scalar(90, CFG) == pytest.approx(1.0)  # capped at 1


def test_per_trade_cap_binds():
    # Huge edge would want a big bet, but per-trade cap must bind.
    res = size_position(
        pop=0.9, max_profit=400, max_loss=100, conviction=85,
        nlv=10_000, per_trade_cap=0.20, remaining_heat_dollars=1e9, cfg=CFG,
    )
    # cap = 0.20 * 10000 = 2000 -> at most 20 contracts of $100 risk
    assert res.capital_at_risk <= 2000 + 1e-9
    assert res.pct_of_nlv <= 0.20 + 1e-9


def test_heat_binds_when_smaller_than_cap():
    res = size_position(
        pop=0.9, max_profit=400, max_loss=100, conviction=85,
        nlv=10_000, per_trade_cap=0.20, remaining_heat_dollars=250, cfg=CFG,
    )
    assert res.contracts == 2  # only $250 heat left, $100/contract
    assert res.binding_constraint == "heat"


def test_below_floor_conviction_sizes_zero():
    res = size_position(
        pop=0.9, max_profit=400, max_loss=100, conviction=50,
        nlv=10_000, per_trade_cap=0.20, remaining_heat_dollars=1e9, cfg=CFG,
    )
    assert res.contracts == 0
    assert res.applied_fraction == 0.0


def test_records_raw_and_applied_fractions():
    res = size_position(
        pop=0.7, max_profit=100, max_loss=400, conviction=76,
        nlv=10_000, per_trade_cap=0.20, remaining_heat_dollars=1e9, cfg=CFG,
    )
    assert res.kelly_fraction >= 0.0
    # applied fraction never exceeds quarter-Kelly * NLV expressed as a fraction
    assert res.applied_fraction <= CFG.kelly_fraction_multiplier * res.kelly_fraction + 1e-9
