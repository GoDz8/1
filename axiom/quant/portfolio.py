"""Portfolio-level aggregation: heat, correlation clusters, Greeks (spec §5.2).

Treats correlated positions as one bet (correlation map from CONFIG) and keeps
aggregate exposures within the mechanical caps. These functions are pure; the
execution guard (spec §5.3) consumes them to accept or reject new trades.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import Config
from .greeks import Greeks


@dataclass(frozen=True)
class OpenPosition:
    symbol: str
    structure_type: str
    contracts: int
    max_loss_total: float   # dollars at risk for the whole position
    net_delta: float = 0.0  # position-level Greeks (already * contracts * 100)
    net_gamma: float = 0.0
    net_vega: float = 0.0
    net_theta: float = 0.0


@dataclass
class PortfolioState:
    nlv: float
    positions: list[OpenPosition] = field(default_factory=list)

    def total_heat_dollars(self) -> float:
        return sum(p.max_loss_total for p in self.positions)

    def total_heat_pct(self) -> float:
        return self.total_heat_dollars() / self.nlv if self.nlv > 0 else float("inf")

    def cluster_heat_dollars(self, cluster: str, cfg: Config) -> float:
        return sum(
            p.max_loss_total for p in self.positions
            if cfg.cluster_for(p.symbol) == cluster
        )

    def net_greeks(self) -> Greeks:
        return Greeks(
            delta=sum(p.net_delta for p in self.positions),
            gamma=sum(p.net_gamma for p in self.positions),
            vega=sum(p.net_vega for p in self.positions),
            theta=sum(p.net_theta for p in self.positions),
            rho=0.0,
        )

    def remaining_heat_dollars(self, cfg: Config) -> float:
        budget = cfg.portfolio.max_portfolio_heat * self.nlv
        return max(0.0, budget - self.total_heat_dollars())


def heat_ok_after(state: PortfolioState, new_max_loss: float, cfg: Config) -> bool:
    """Would adding ``new_max_loss`` keep total heat within the cap?"""
    if state.nlv <= 0:
        return False
    projected = (state.total_heat_dollars() + new_max_loss) / state.nlv
    return projected <= cfg.portfolio.max_portfolio_heat + 1e-9


def correlation_ok_after(
    state: PortfolioState, symbol: str, new_max_loss: float, cfg: Config
) -> bool:
    """Would adding the trade keep its correlation cluster within the cap?"""
    if state.nlv <= 0:
        return False
    cluster = cfg.cluster_for(symbol)
    if cluster is None:
        return True  # uncorrelated single name — only heat cap applies
    projected = (state.cluster_heat_dollars(cluster, cfg) + new_max_loss) / state.nlv
    return projected <= cfg.portfolio.max_correlation_cluster + 1e-9


def greeks_bands_ok_after(
    state: PortfolioState, add: Greeks, cfg: Config
) -> bool:
    """Would adding ``add`` (position-level Greeks) stay within the bands?"""
    if state.nlv <= 0:
        return False
    net = state.net_greeks()
    bands = cfg.greeks_bands
    delta = (net.delta + add.delta) / state.nlv
    gamma = (net.gamma + add.gamma) / state.nlv
    vega = (net.vega + add.vega) / state.nlv
    theta = (net.theta + add.theta) / state.nlv
    return (
        abs(delta) <= bands.max_abs_delta_per_nlv + 1e-12
        and abs(gamma) <= bands.max_abs_gamma_per_nlv + 1e-12
        and abs(vega) <= bands.max_abs_vega_per_nlv + 1e-12
        and theta >= bands.min_theta_per_nlv - 1e-12
    )
