"""Pre-trade guard + kill-switch tests (spec §5.3/§5.4/§5.6)."""

from axiom.config import Config
from axiom.data.robinhood_mcp import AccountSnapshot
from axiom.execution.guard import (
    KillSwitchState, evaluate, kill_switch_tripped,
)
from axiom.quant.ev import EVResult
from axiom.quant.payoff import Leg, Right, Side, StructureType
from axiom.quant.portfolio import OpenPosition, PortfolioState
from axiom.strategy.candidates import Candidate
from axiom.strategy.gating import StrategyFamily

CFG = Config()


def _candidate(structure_type=StructureType.PUT_CREDIT_SPREAD, max_loss=100.0,
               accepted=True):
    ev = EVResult(ev_gross=30, ev_after_slippage=25, pop=0.7, max_profit=100,
                  max_loss=max_loss, breakevens=(94.0,),
                  ev_per_dollar_risk=0.25, expected_return_on_capital=0.25)
    fam = (StrategyFamily.DEBIT_SPREAD
           if structure_type in (StructureType.PUT_DEBIT_SPREAD, StructureType.CALL_DEBIT_SPREAD)
           else StrategyFamily.CREDIT_SPREAD)
    legs = [Leg(Side.SELL, Right.PUT, 95, 2.0), Leg(Side.BUY, Right.PUT, 90, 1.0)]
    return Candidate("NVDA", structure_type, fam, legs, 35, 100.0, 0.4, ev,
                     rejected_reason=None if accepted else "ev gate")


def _acct(nlv=5000.0, bp=5000.0, dt=0):
    return AccountSnapshot(nlv=nlv, buying_power=bp, day_trades_used=dt)


def test_clean_trade_approved():
    res = evaluate(_candidate(), 2, _acct(), PortfolioState(5000.0), 40,
                   KillSwitchState(), CFG)
    assert res.approved


def test_per_trade_cap_blocks():
    # cap = 20% of 1000 = 200; 3 contracts * 100 = 300 > cap
    res = evaluate(_candidate(max_loss=100), 3, _acct(nlv=1000, bp=10000),
                   PortfolioState(1000.0), 40, KillSwitchState(), CFG)
    assert not res.approved
    assert not res.checks.per_trade_cap_ok


def test_earnings_blocks_debit_buy():
    res = evaluate(_candidate(StructureType.PUT_DEBIT_SPREAD), 1, _acct(),
                   PortfolioState(5000.0), days_to_earnings=5,
                   ks=KillSwitchState(), cfg=CFG)
    assert not res.checks.earnings_rule_ok
    assert not res.approved


def test_earnings_does_not_block_credit_sell():
    res = evaluate(_candidate(StructureType.PUT_CREDIT_SPREAD), 1, _acct(),
                   PortfolioState(5000.0), days_to_earnings=5,
                   ks=KillSwitchState(), cfg=CFG)
    assert res.checks.earnings_rule_ok


def test_heat_cap_blocks():
    # heat cap = 25% * 5000 = 1250; adding 1*100=100 -> 1300 > 1250
    pf = PortfolioState(5000.0, [OpenPosition("SPY", "X", 1, 1200)])
    res = evaluate(_candidate(max_loss=100), 1, _acct(), pf, 40,
                   KillSwitchState(), CFG)
    assert not res.checks.portfolio_heat_ok


def test_kill_switch_blocks_all():
    ks = KillSwitchState(consecutive_losses=4)
    tripped, reason = kill_switch_tripped(ks, CFG)
    assert tripped and reason
    res = evaluate(_candidate(), 1, _acct(), PortfolioState(5000.0), 40, ks, CFG)
    assert not res.checks.kill_switch_ok
    assert not res.approved


def test_pdt_budget_exhausted_blocks():
    res = evaluate(_candidate(), 1, _acct(nlv=5000, dt=3), PortfolioState(5000.0),
                   40, KillSwitchState(), CFG)
    assert not res.checks.pdt_ok


def test_rejected_candidate_fails_liquidity():
    res = evaluate(_candidate(accepted=False), 1, _acct(),
                   PortfolioState(5000.0), 40, KillSwitchState(), CFG)
    assert not res.checks.liquidity_ok
    assert not res.approved
