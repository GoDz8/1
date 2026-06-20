"""EV / POP / expectancy tests."""

import numpy as np
import pytest

from axiom.quant.ev import expected_value, terminal_distribution
from axiom.quant.payoff import Leg, Right, Side


def test_terminal_distribution_normalized():
    grid, w = terminal_distribution(100, 0.5, 0.2, 0.04)
    assert w.sum() == pytest.approx(1.0, abs=1e-6)
    # Risk-neutral mean of S_T ~ forward = S0 * e^{(r-q)t}
    mean = float(np.dot(grid, w))
    assert mean == pytest.approx(100 * np.exp(0.04 * 0.5), rel=1e-3)


def test_pop_between_zero_and_one():
    legs = [Leg(Side.SELL, Right.PUT, 95, 2.0), Leg(Side.BUY, Right.PUT, 90, 1.0)]
    res = expected_value(legs, spot=100, t=0.25, vol=0.3, rate=0.04)
    assert 0.0 <= res.pop <= 1.0
    # An OTM put credit spread should have POP well above 0.5.
    assert res.pop > 0.5


def test_friction_reduces_ev():
    legs = [Leg(Side.SELL, Right.PUT, 95, 2.0), Leg(Side.BUY, Right.PUT, 90, 1.0)]
    clean = expected_value(legs, 100, 0.25, 0.3, 0.04, friction_cost=0.0)
    dirty = expected_value(legs, 100, 0.25, 0.3, 0.04, friction_cost=20.0)
    assert dirty.ev_after_slippage == pytest.approx(clean.ev_after_slippage - 20.0, abs=1e-6)
    assert dirty.ev_per_dollar_risk < clean.ev_per_dollar_risk


def test_ev_per_dollar_risk_uses_max_loss():
    legs = [Leg(Side.SELL, Right.PUT, 95, 2.0), Leg(Side.BUY, Right.PUT, 90, 1.0)]
    res = expected_value(legs, 100, 0.25, 0.3, 0.04)
    assert res.max_loss == pytest.approx(400.0, abs=1.0)
    assert res.ev_per_dollar_risk == pytest.approx(res.ev_after_slippage / res.max_loss, abs=1e-6)


def test_deep_otm_sale_is_positive_ev_gross():
    # Selling a far-OTM credit spread for real premium should be +EV gross at fair vol.
    legs = [Leg(Side.SELL, Right.PUT, 80, 0.50), Leg(Side.BUY, Right.PUT, 75, 0.25)]
    res = expected_value(legs, 100, 0.25, 0.2, 0.04)
    assert res.ev_gross > 0
