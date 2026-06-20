"""Portfolio aggregation tests: heat, correlation cluster, Greeks bands."""

from axiom.config import Config
from axiom.quant.greeks import Greeks
from axiom.quant.portfolio import (
    OpenPosition, PortfolioState, correlation_ok_after, greeks_bands_ok_after,
    heat_ok_after,
)

CFG = Config()


def test_total_heat():
    state = PortfolioState(nlv=10_000, positions=[
        OpenPosition("SPY", "PUT_CREDIT_SPREAD", 1, 400),
        OpenPosition("NVDA", "IRON_CONDOR", 1, 600),
    ])
    assert state.total_heat_dollars() == 1000
    assert state.total_heat_pct() == 0.10


def test_heat_cap_blocks_over_limit():
    state = PortfolioState(nlv=10_000, positions=[
        OpenPosition("SPY", "X", 1, 2400),
    ])
    # 25% cap = 2500; adding 200 -> 2600 -> over
    assert heat_ok_after(state, 100, CFG) is True
    assert heat_ok_after(state, 200, CFG) is False


def test_correlation_cluster_cap():
    # ai_semis cluster cap = 12% of 10000 = 1200
    state = PortfolioState(nlv=10_000, positions=[
        OpenPosition("NVDA", "X", 1, 1000),
    ])
    assert correlation_ok_after(state, "AMD", 150, CFG) is True   # 1150 <= 1200
    assert correlation_ok_after(state, "AMD", 300, CFG) is False  # 1300 > 1200


def test_uncorrelated_symbol_passes_correlation():
    state = PortfolioState(nlv=10_000, positions=[])
    # A symbol in no cluster is only bound by heat, not correlation.
    assert correlation_ok_after(state, "XYZ", 5000, CFG) is True


def test_greeks_bands():
    state = PortfolioState(nlv=10_000, positions=[])
    small = Greeks(delta=100, gamma=10, vega=100, theta=50, rho=0)
    assert greeks_bands_ok_after(state, small, CFG) is True
    # delta band = 0.50/$ * 10000 = 5000 net delta; 6000 should fail
    big = Greeks(delta=6000, gamma=0, vega=0, theta=0, rho=0)
    assert greeks_bands_ok_after(state, big, CFG) is False


def test_remaining_heat_dollars():
    state = PortfolioState(nlv=10_000, positions=[
        OpenPosition("SPY", "X", 1, 1000),
    ])
    # budget 2500 - 1000 used = 1500 remaining
    assert state.remaining_heat_dollars(CFG) == 1500
