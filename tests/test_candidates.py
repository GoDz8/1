"""Candidate construction + EV-gate tests."""

from axiom.config import Config
from axiom.strategy.candidates import build_candidates
from axiom.strategy.gating import StrategyFamily

CFG = Config()


def test_credit_spread_candidate_is_defined_risk(adapter):
    chain = adapter.get_option_chain("NVDA", 35)
    cands = build_candidates(chain, (StrategyFamily.CREDIT_SPREAD,), CFG, 35)
    assert cands
    for c in cands:
        assert c.ev.max_loss > 0          # defined risk
        assert 0.0 <= c.ev.pop <= 1.0


def test_ev_gate_flags_rejections(adapter):
    chain = adapter.get_option_chain("AAPL", 35)
    cands = build_candidates(chain, (StrategyFamily.CREDIT_SPREAD,), CFG, 35)
    # Every candidate is either accepted or carries a rejection reason (logged).
    for c in cands:
        assert c.accepted or c.rejected_reason


def test_iron_condor_has_four_legs(adapter):
    chain = adapter.get_option_chain("MSFT", 35)
    cands = build_candidates(chain, (StrategyFamily.IRON_CONDOR,), CFG, 35)
    assert cands
    assert len(cands[0].legs) == 4


def test_debit_spread_pays_premium(adapter):
    chain = adapter.get_option_chain("AMD", 35)
    cands = build_candidates(chain, (StrategyFamily.DEBIT_SPREAD,), CFG, 35)
    assert cands
    from axiom.quant.payoff import net_credit
    # A debit structure has negative net credit (pays to enter).
    assert net_credit(cands[0].legs) < 0
