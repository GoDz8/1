"""Greeks tests: golden ATM values + finite-difference cross-checks vs BS price."""

import pytest

from axiom.quant.black_scholes import BSInputs, price
from axiom.quant.greeks import all_greeks, delta, gamma, theta, vega


BASE = BSInputs(spot=100, strike=100, t=1.0, rate=0.05, vol=0.20)


def fd_delta(args, is_call, h=1e-4):
    up = price(BSInputs(args.spot + h, args.strike, args.t, args.rate, args.vol, args.dividend), is_call)
    dn = price(BSInputs(args.spot - h, args.strike, args.t, args.rate, args.vol, args.dividend), is_call)
    return (up - dn) / (2 * h)


def fd_gamma(args, is_call, h=1e-2):
    up = price(BSInputs(args.spot + h, args.strike, args.t, args.rate, args.vol, args.dividend), is_call)
    mid = price(args, is_call)
    dn = price(BSInputs(args.spot - h, args.strike, args.t, args.rate, args.vol, args.dividend), is_call)
    return (up - 2 * mid + dn) / (h * h)


def fd_vega(args, is_call, h=1e-5):
    up = price(BSInputs(args.spot, args.strike, args.t, args.rate, args.vol + h, args.dividend), is_call)
    dn = price(BSInputs(args.spot, args.strike, args.t, args.rate, args.vol - h, args.dividend), is_call)
    return (up - dn) / (2 * h)


def fd_theta(args, is_call, h=1e-5):
    # theta per year = -dV/dT (T = time to expiry)
    up = price(BSInputs(args.spot, args.strike, args.t + h, args.rate, args.vol, args.dividend), is_call)
    dn = price(BSInputs(args.spot, args.strike, args.t - h, args.rate, args.vol, args.dividend), is_call)
    return -(up - dn) / (2 * h)


def test_atm_call_delta_golden():
    assert delta(BASE, is_call=True) == pytest.approx(0.6368, abs=1e-3)
    assert delta(BASE, is_call=False) == pytest.approx(0.6368 - 1.0, abs=1e-3)


@pytest.mark.parametrize("is_call", [True, False])
def test_delta_matches_fd(is_call):
    assert delta(BASE, is_call) == pytest.approx(fd_delta(BASE, is_call), abs=1e-5)


def test_gamma_matches_fd():
    # gamma is the same for calls and puts
    assert gamma(BASE) == pytest.approx(fd_gamma(BASE, True), abs=1e-4)
    assert gamma(BASE) == pytest.approx(fd_gamma(BASE, False), abs=1e-4)


@pytest.mark.parametrize("is_call", [True, False])
def test_vega_matches_fd(is_call):
    assert vega(BASE) == pytest.approx(fd_vega(BASE, is_call), abs=1e-2)


@pytest.mark.parametrize("is_call", [True, False])
def test_theta_matches_fd(is_call):
    assert theta(BASE, is_call) == pytest.approx(fd_theta(BASE, is_call), abs=1e-2)


def test_long_option_theta_negative():
    assert theta(BASE, is_call=True) < 0
    assert theta(BASE, is_call=False) < 0


def test_all_greeks_bundle():
    g = all_greeks(BASE, is_call=True)
    assert g.delta == pytest.approx(delta(BASE, True))
    assert g.vega == pytest.approx(vega(BASE))
