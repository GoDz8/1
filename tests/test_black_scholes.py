"""Golden-value tests for Black-Scholes pricing (textbook references)."""

import math

import pytest

from axiom.quant.black_scholes import (
    BSInputs, call_price, put_price, d1, d2, forward, price,
)


def test_atm_call_put_golden():
    # S=100, K=100, T=1, r=5%, vol=20%, q=0 -> well-known textbook values.
    args = BSInputs(spot=100, strike=100, t=1.0, rate=0.05, vol=0.20)
    assert call_price(args) == pytest.approx(10.4506, abs=1e-3)
    assert put_price(args) == pytest.approx(5.5735, abs=1e-3)


def test_d1_d2():
    args = BSInputs(spot=100, strike=100, t=1.0, rate=0.05, vol=0.20)
    assert d1(args) == pytest.approx(0.35, abs=1e-6)
    assert d2(args) == pytest.approx(0.15, abs=1e-6)


def test_put_call_parity():
    args = BSInputs(spot=120, strike=110, t=0.5, rate=0.03, vol=0.35, dividend=0.01)
    c = call_price(args)
    p = put_price(args)
    lhs = c - p
    rhs = (args.spot * math.exp(-args.dividend * args.t)
           - args.strike * math.exp(-args.rate * args.t))
    assert lhs == pytest.approx(rhs, abs=1e-8)


def test_zero_time_returns_intrinsic():
    args = BSInputs(spot=110, strike=100, t=0.0, rate=0.05, vol=0.2)
    assert call_price(args) == pytest.approx(10.0)
    assert put_price(args) == pytest.approx(0.0)


def test_zero_vol_returns_discounted_intrinsic():
    args = BSInputs(spot=110, strike=100, t=1.0, rate=0.05, vol=0.0)
    expected = 110 - 100 * math.exp(-0.05)
    assert call_price(args) == pytest.approx(expected, abs=1e-9)


def test_monotonic_in_vol():
    lo = BSInputs(100, 100, 1.0, 0.05, 0.10)
    hi = BSInputs(100, 100, 1.0, 0.05, 0.40)
    assert call_price(hi) > call_price(lo)
    assert put_price(hi) > put_price(lo)


def test_forward():
    args = BSInputs(100, 100, 1.0, 0.05, 0.2, dividend=0.02)
    assert forward(args) == pytest.approx(100 * math.exp(0.03), abs=1e-9)


def test_invalid_inputs_raise():
    with pytest.raises(ValueError):
        price(BSInputs(-1, 100, 1.0, 0.05, 0.2), is_call=True)
    with pytest.raises(ValueError):
        price(BSInputs(100, 100, -1.0, 0.05, 0.2), is_call=True)
