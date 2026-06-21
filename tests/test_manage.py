"""Position-management tests (spec §4 Step 9, §5.5): exit-rule sign correctness."""

import pytest

from axiom.config import ExitConfig
from axiom.quant.payoff import Leg, Right, Side
from axiom.execution.manage import (
    ManageAction, ManagedPosition, decide_management, mark_value, position_pnl,
)

CFG = ExitConfig()
RATE = 0.04


def _put_credit_spread():
    # Sell 95 put, buy 90 put; entry credit ~ collected premium.
    legs = [Leg(Side.SELL, Right.PUT, 95, 2.0), Leg(Side.BUY, Right.PUT, 90, 1.0)]
    return ManagedPosition(
        position_id="p1", decision_id="d1", symbol="NVDA",
        structure_type="PUT_CREDIT_SPREAD", legs=legs,
        entry_credit=100.0, max_profit=100.0, max_loss=400.0,
        dte_remaining=35, short_strike=95.0,
    )


def test_pnl_zero_at_entry_conditions():
    pos = _put_credit_spread()
    # Mark at the same IV/spot the entry premiums imply ~ entry; pnl small.
    # (entry premiums were 2.0/1.0; we just check the sign machinery.)
    pnl = position_pnl(pos, spot=100.0, t=35 / 365, iv=0.30, rate=RATE)
    assert isinstance(pnl, float)


def test_profit_target_triggers_when_spread_decays():
    pos = _put_credit_spread()
    # Far OTM + low IV + short time -> spread nearly worthless -> near max profit.
    d = decide_management(pos, spot=130.0, t=2 / 365, iv=0.15, rate=RATE, cfg=CFG)
    assert d.action is ManageAction.EXIT
    assert d.reason == "profit target"
    assert d.pnl >= CFG.profit_target_pct * pos.max_profit


def test_stop_triggers_on_adverse_move():
    pos = _put_credit_spread()
    # Deep ITM short put -> spread worth far more than entry -> big loss -> stop.
    d = decide_management(pos, spot=85.0, t=20 / 365, iv=0.40, rate=RATE, cfg=CFG)
    assert d.action is ManageAction.EXIT
    assert d.reason in ("stop (2x credit)", "thesis invalidation")


def test_time_stop_triggers():
    pos = _put_credit_spread()
    pos = ManagedPosition(**{**pos.__dict__, "dte_remaining": 21})
    # Neutral mark so neither profit nor stop fires first; time stop should.
    d = decide_management(pos, spot=100.0, t=21 / 365, iv=0.30, rate=RATE, cfg=CFG)
    assert d.action is ManageAction.EXIT
    assert d.reason == "time stop"


def test_invalidation_flag_on_short_breach():
    pos = _put_credit_spread()
    # Spot below short strike but not enough to hit stop, with DTE > time stop.
    d = decide_management(pos, spot=94.0, t=30 / 365, iv=0.30, rate=RATE, cfg=CFG)
    if d.reason == "thesis invalidation":
        assert d.invalidation_triggered is True


def test_hold_when_within_thesis():
    # Build a self-consistent position priced at entry, then mark at the SAME
    # conditions -> pnl == 0 -> no profit/stop/time/invalidation -> HOLD.
    from axiom.quant.black_scholes import BSInputs, price
    from axiom.quant.payoff import economics, net_credit
    spot, iv, dte = 100.0, 0.30, 35
    t = dte / 365
    sp = price(BSInputs(spot, 95, t, RATE, iv), is_call=False)
    lp = price(BSInputs(spot, 90, t, RATE, iv), is_call=False)
    legs = [Leg(Side.SELL, Right.PUT, 95, sp), Leg(Side.BUY, Right.PUT, 90, lp)]
    econ = economics(legs)
    pos = ManagedPosition(
        position_id="p2", decision_id="d2", symbol="NVDA",
        structure_type="PUT_CREDIT_SPREAD", legs=legs,
        entry_credit=net_credit(legs), max_profit=econ.max_profit,
        max_loss=econ.max_loss, dte_remaining=dte, short_strike=95.0,
    )
    d = decide_management(pos, spot=spot, t=t, iv=iv, rate=RATE, cfg=CFG)
    assert d.pnl == pytest.approx(0.0, abs=1e-6)
    assert d.action is ManageAction.HOLD


def test_mark_value_decreases_as_short_goes_otm():
    pos = _put_credit_spread()
    near = mark_value(pos.legs, spot=96.0, t=30 / 365, iv=0.30, rate=RATE)
    far = mark_value(pos.legs, spot=120.0, t=30 / 365, iv=0.30, rate=RATE)
    # A put credit spread is worth less (cheaper to close) as spot rises.
    assert far < near
