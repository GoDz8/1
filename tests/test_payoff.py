"""Hand-computed payoff/economics tests for the defined-risk structures."""

import pytest

from axiom.quant.payoff import Leg, Right, Side, economics, net_credit


def put_credit_spread():
    # Sell 95 put @2.00, buy 90 put @1.00 -> credit 1.00/share, width 5.
    return [
        Leg(Side.SELL, Right.PUT, 95, 2.00),
        Leg(Side.BUY, Right.PUT, 90, 1.00),
    ]


def call_debit_spread():
    # Buy 100 call @3.00, sell 105 call @1.00 -> debit 2.00/share, width 5.
    return [
        Leg(Side.BUY, Right.CALL, 100, 3.00),
        Leg(Side.SELL, Right.CALL, 105, 1.00),
    ]


def iron_condor():
    return [
        Leg(Side.SELL, Right.PUT, 95, 2.00),
        Leg(Side.BUY, Right.PUT, 90, 1.00),
        Leg(Side.SELL, Right.CALL, 105, 2.00),
        Leg(Side.BUY, Right.CALL, 110, 1.00),
    ]


def test_put_credit_spread_economics():
    econ = economics(put_credit_spread())
    assert econ.is_credit
    assert econ.net_credit == pytest.approx(100.0)
    assert econ.max_profit == pytest.approx(100.0, abs=1.0)
    assert econ.max_loss == pytest.approx(400.0, abs=1.0)
    assert econ.breakevens[0] == pytest.approx(94.0, abs=0.05)


def test_call_debit_spread_economics():
    econ = economics(call_debit_spread())
    assert not econ.is_credit
    assert econ.net_credit == pytest.approx(-200.0)
    assert econ.max_profit == pytest.approx(300.0, abs=1.0)
    assert econ.max_loss == pytest.approx(200.0, abs=1.0)
    assert econ.breakevens[0] == pytest.approx(102.0, abs=0.05)


def test_iron_condor_economics():
    econ = economics(iron_condor())
    assert econ.is_credit
    assert econ.net_credit == pytest.approx(200.0)
    assert econ.max_profit == pytest.approx(200.0, abs=1.0)
    assert econ.max_loss == pytest.approx(300.0, abs=1.0)
    bes = sorted(econ.breakevens)
    assert bes[0] == pytest.approx(93.0, abs=0.1)
    assert bes[1] == pytest.approx(107.0, abs=0.1)


def test_net_credit_sign():
    assert net_credit(put_credit_spread()) > 0
    assert net_credit(call_debit_spread()) < 0


def test_empty_structure_raises():
    with pytest.raises(ValueError):
        economics([])
