"""IV solver tests: price -> IV -> price round-trips and un-invertible cases."""

import pytest

from axiom.quant.black_scholes import BSInputs, price
from axiom.quant.iv_solver import implied_vol


@pytest.mark.parametrize("true_vol", [0.08, 0.15, 0.20, 0.45, 0.90])
@pytest.mark.parametrize("is_call", [True, False])
def test_roundtrip(true_vol, is_call):
    args = BSInputs(spot=100, strike=105, t=0.5, rate=0.04, vol=true_vol)
    px = price(args, is_call)
    solved = implied_vol(px, 100, 105, 0.5, 0.04, is_call)
    assert solved == pytest.approx(true_vol, abs=1e-4)


def test_price_below_intrinsic_returns_none():
    # Call worth less than discounted intrinsic is un-invertible -> unknown.
    assert implied_vol(0.001, 200, 100, 1.0, 0.05, is_call=True) is None


def test_nonpositive_inputs_return_none():
    assert implied_vol(0.0, 100, 100, 1.0, 0.05, True) is None
    assert implied_vol(5.0, 100, 100, 0.0, 0.05, True) is None


def test_price_above_upper_bound_returns_none():
    # A call cannot be worth more than (discounted) spot.
    assert implied_vol(150.0, 100, 100, 1.0, 0.05, is_call=True) is None
