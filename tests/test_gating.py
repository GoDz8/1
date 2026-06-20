"""Strategy gating tests (spec §2/§3 hard rules)."""

from axiom.config import GatingConfig
from axiom.data.catalysts import Catalyst
from axiom.strategy.gating import GatingInput, StrategyFamily, gate

CFG = GatingConfig()


def _inp(**over):
    data = dict(symbol="X", iv_rank=0.6, days_to_earnings=40, catalyst=None,
                open_interest_ok=True, spread_ok=True)
    data.update(over)
    return GatingInput(**data)


def test_high_iv_rank_allows_credit_and_condor():
    res = gate(_inp(iv_rank=0.7), CFG)
    assert StrategyFamily.CREDIT_SPREAD in res.allowed
    assert StrategyFamily.IRON_CONDOR in res.allowed


def test_low_iv_with_dated_catalyst_allows_debit():
    cat = Catalyst("X", "product", "2026-07-01", "launch", directional=True)
    res = gate(_inp(iv_rank=0.2, catalyst=cat), CFG)
    assert StrategyFamily.DEBIT_SPREAD in res.allowed


def test_low_iv_without_catalyst_forbids_buying():
    res = gate(_inp(iv_rank=0.2, catalyst=None), CFG)
    assert StrategyFamily.DEBIT_SPREAD not in res.allowed
    assert res.is_empty  # below sell floor, no catalyst -> nothing


def test_earnings_blocks_debit_even_with_catalyst():
    cat = Catalyst("X", "product", "2026-07-01", "launch", directional=True)
    res = gate(_inp(iv_rank=0.2, catalyst=cat, days_to_earnings=5), CFG)
    assert StrategyFamily.DEBIT_SPREAD not in res.allowed


def test_unknown_iv_rank_fails_closed():
    res = gate(_inp(iv_rank=None), CFG)
    assert res.is_empty


def test_bad_liquidity_fails_closed():
    res = gate(_inp(open_interest_ok=False), CFG)
    assert res.is_empty
