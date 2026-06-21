"""Central configuration for AXIOM.

Every tunable knob the spec calls out lives here as a CONFIG value. The risk
caps in this file are *mechanical and inviolable* (spec §5.6): no other module
may relax them, and the LLM reasoning layer can never reach past them. Changing
a cap is an operator action (edit this file), never an AI decision.

All percentages are expressed as fractions of net liquidation value (NLV)
unless noted otherwise.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from enum import Enum


# --------------------------------------------------------------------------- #
# Enumerations
# --------------------------------------------------------------------------- #
class RiskProfile(str, Enum):
    """Operator-selected aggression dial (spec §5.0). Sets per-trade cap."""

    CONSERVATIVE = "CONSERVATIVE"
    BALANCED = "BALANCED"
    AGGRESSIVE = "AGGRESSIVE"  # operator default


# Per-trade max-loss cap as a fraction of NLV, by profile (spec §5.0).
PER_TRADE_CAP_BY_PROFILE: dict[RiskProfile, float] = {
    RiskProfile.CONSERVATIVE: 0.08,
    RiskProfile.BALANCED: 0.12,
    RiskProfile.AGGRESSIVE: 0.20,
}


class Tier(int, Enum):
    """Capital tier (spec §5.0). Unlocks capability as NLV grows."""

    SEED = 0          # NLV < ~$700: single position, AI-allocated
    CONSTRAINED = 1   # ~$700-3k: one position
    DEVELOPING = 2    # ~$3k-10k: 2-3 uncorrelated positions
    ESTABLISHED = 3   # ~$10k-25k: fuller book, PDT-limited
    UNRESTRICTED = 4  # >=$25k: full system, PDT lifted


@dataclass(frozen=True)
class TierRule:
    """Per-tier operating envelope."""

    tier: Tier
    min_nlv: float
    max_concurrent_positions: int
    correlated_stacking_allowed: bool
    pdt_limited: bool  # True while NLV < $25k (margin acct, <3 day-trades / 5d)


# Ordered high -> low so resolution picks the first tier whose floor we clear.
TIER_RULES: tuple[TierRule, ...] = (
    TierRule(Tier.UNRESTRICTED, 25_000.0, max_concurrent_positions=12,
             correlated_stacking_allowed=True, pdt_limited=False),
    TierRule(Tier.ESTABLISHED, 10_000.0, max_concurrent_positions=6,
             correlated_stacking_allowed=True, pdt_limited=True),
    TierRule(Tier.DEVELOPING, 3_000.0, max_concurrent_positions=3,
             correlated_stacking_allowed=True, pdt_limited=True),
    TierRule(Tier.CONSTRAINED, 700.0, max_concurrent_positions=1,
             correlated_stacking_allowed=False, pdt_limited=True),
    TierRule(Tier.SEED, 0.0, max_concurrent_positions=1,
             correlated_stacking_allowed=False, pdt_limited=True),
)


def resolve_tier(nlv: float) -> TierRule:
    """Return the TierRule whose ``min_nlv`` floor ``nlv`` clears (spec §5.0)."""
    for rule in TIER_RULES:
        if nlv >= rule.min_nlv:
            return rule
    return TIER_RULES[-1]  # SEED is the floor


# --------------------------------------------------------------------------- #
# Greeks bands (spec §5.2): net portfolio exposure kept within these per $ NLV.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class GreeksBands:
    # Bands are |net greek per $1 of NLV|. Conservative defaults; tune in Ph2.
    max_abs_delta_per_nlv: float = 0.50
    max_abs_vega_per_nlv: float = 0.05
    max_abs_gamma_per_nlv: float = 0.02
    # Theta is desirable when selling premium; we cap only the *negative* side.
    min_theta_per_nlv: float = -0.02


# --------------------------------------------------------------------------- #
# Strategy gating thresholds (spec §2 / §3).
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class GatingConfig:
    iv_rank_sell_floor: float = 0.50    # IV Rank >= 50 -> sell premium candidate
    iv_rank_buy_ceiling: float = 0.30   # IV Rank < 30 (+catalyst) -> buy premium
    earnings_block_days: int = 14       # premium-buy blocked within this window
    earnings_flag_days: int = 14        # flag earnings in this window
    min_open_interest: int = 500        # liquidity floor
    max_bid_ask_width_pct: float = 0.10 # bid-ask width / mid; wider -> drop
    # Default mechanical hypotheses (spec §2.6) — STARTING points, validated by
    # the backtester before going live, not facts assumed true.
    target_dte_low: int = 30
    target_dte_high: int = 45


# --------------------------------------------------------------------------- #
# EV / expectancy gate (spec §2.5 / §4.4).
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class EVConfig:
    min_ev_after_slippage: float = 0.0      # EV must be strictly > this
    min_ev_per_dollar_risk: float = 0.03    # minimum expectancy threshold
    # At tiny tiers, reject if modeled slippage eats more than this fraction of
    # expected EV (spec §5.0 friction discipline).
    max_slippage_to_ev_ratio: float = 0.35
    # The volatility risk premium (spec §2.1): realized vol persistently runs
    # BELOW implied. Premium is COLLECTED at IV, but the EV terminal-price
    # distribution must use the realized-vol FORECAST, not IV — otherwise the
    # model assumes risk-neutral == real and zeroes out the very edge AXIOM
    # harvests. This ratio (realized/implied) is the VRP assumption; it is a
    # STARTING hypothesis (§2.6) the backtester and Phase-2 paper data validate
    # and recalibrate. With real data, realized vol comes from yfinance HV.
    realized_vol_ratio: float = 0.85


# --------------------------------------------------------------------------- #
# Slippage model (spec §2.5).
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SlippageConfig:
    # Fraction of the bid-ask spread paid on entry/exit (mid-to-natural).
    # 0.5 == fill at mid; 1.0 == fill at the natural (full half-spread paid).
    spread_fraction_paid: float = 0.5
    per_contract_fee: float = 0.0       # Robinhood options commission
    per_contract_exchange_fee: float = 0.03  # modeled regulatory/exchange fees


# --------------------------------------------------------------------------- #
# Sizing (spec §5.1).
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SizingConfig:
    kelly_fraction_multiplier: float = 0.25   # quarter-Kelly
    conviction_floor: int = 70                # below this -> PASS (spec §4.1)
    high_conviction: int = 85                 # >=this -> top of quarter-Kelly band
    # Conviction maps linearly from [floor, high] -> [low, 1.0] of quarter-Kelly.
    conviction_low_scalar: float = 0.40


# --------------------------------------------------------------------------- #
# Portfolio limits (spec §5.2) and kill switches (spec §5.4).
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ExitConfig:
    """Exit discipline (spec §5.5). STARTING hypotheses validated by the
    backtester / walk-forward meta-loop, not assumed facts."""

    # Credit structures.
    profit_target_pct: float = 0.50     # close at ~50% of max profit
    stop_mult_credit: float = 2.0       # stop at ~2x credit received (loss)
    time_stop_dte: int = 21             # manage tested side at ~21 DTE
    # Debit structures.
    debit_profit_target_pct: float = 0.75
    debit_stop_pct: float = 0.50        # cut at 50% of debit lost


@dataclass(frozen=True)
class LearningConfig:
    """Continuous-learning loop knobs (spec §11). All adaptations are slow,
    sample-gated, and can NEVER touch the §5.6 risk envelope."""

    min_resolved_for_calibration: int = 50  # apply calibration map only above N
    min_bucket_n: int = 20                  # never act on a bucket below this N
    shrinkage_k: int = 20                   # Bayesian shrinkage strength
    time_decay_halflife_days: float = 90.0  # recent data weighted more
    retrieval_k: int = 5                    # similar past cases injected
    reliability_bins: int = 10
    # A bucket is suppressed only if its shrunk expectancy is significantly < 0.
    suppress_expectancy_threshold: float = 0.0


@dataclass(frozen=True)
class PortfolioLimits:
    max_portfolio_heat: float = 0.25     # sum of open max-losses / NLV
    max_correlation_cluster: float = 0.12  # aggregate max-loss per cluster / NLV


@dataclass(frozen=True)
class KillSwitchConfig:
    max_daily_loss: float = 0.10          # realized daily loss >= this -> halt
    max_drawdown_from_hwm: float = 0.20   # drawdown from high-water mark -> halt
    max_consecutive_losses: int = 4
    vix_spike_threshold: float = 35.0     # de-risk above this
    max_mcp_error_rate: float = 0.25      # data-feed health


# --------------------------------------------------------------------------- #
# PDT (spec §10).
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class PDTConfig:
    pdt_equity_threshold: float = 25_000.0
    max_day_trades_per_5_business_days: int = 3


# --------------------------------------------------------------------------- #
# Correlation map (spec §5.2). Symbols sharing a cluster are treated as one bet.
# --------------------------------------------------------------------------- #
DEFAULT_CORRELATION_CLUSTERS: dict[str, tuple[str, ...]] = {
    "ai_semis": ("NVDA", "TSM", "AVGO", "CRDO", "AMD", "MU", "SMCI"),
    "mega_tech": ("AAPL", "MSFT", "GOOGL", "AMZN", "META"),
    "long_beta": ("SPY", "QQQ", "IWM", "DIA"),
    "energy": ("XLE", "XOM", "CVX"),
}

DEFAULT_WATCHLIST: tuple[str, ...] = (
    "SPY", "QQQ", "IWM", "NVDA", "AAPL", "MSFT", "AMD", "TSLA", "META",
)


# --------------------------------------------------------------------------- #
# Top-level CONFIG
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Config:
    risk_profile: RiskProfile = RiskProfile.AGGRESSIVE
    db_path: str = "axiom.db"
    llm_model: str = "claude-opus-4-8"
    risk_free_rate: float = 0.04  # annualized; used by BS pricing

    greeks_bands: GreeksBands = field(default_factory=GreeksBands)
    gating: GatingConfig = field(default_factory=GatingConfig)
    ev: EVConfig = field(default_factory=EVConfig)
    slippage: SlippageConfig = field(default_factory=SlippageConfig)
    sizing: SizingConfig = field(default_factory=SizingConfig)
    exit_rules: ExitConfig = field(default_factory=ExitConfig)
    learning: LearningConfig = field(default_factory=LearningConfig)
    portfolio: PortfolioLimits = field(default_factory=PortfolioLimits)
    kill_switch: KillSwitchConfig = field(default_factory=KillSwitchConfig)
    pdt: PDTConfig = field(default_factory=PDTConfig)

    correlation_clusters: dict[str, tuple[str, ...]] = field(
        default_factory=lambda: dict(DEFAULT_CORRELATION_CLUSTERS)
    )
    watchlist: tuple[str, ...] = DEFAULT_WATCHLIST

    @property
    def per_trade_cap(self) -> float:
        """Per-trade max-loss cap as a fraction of NLV (spec §5.0/§5.1)."""
        return PER_TRADE_CAP_BY_PROFILE[self.risk_profile]

    def cluster_for(self, symbol: str) -> str | None:
        """Return the correlation cluster a symbol belongs to, if any."""
        for name, members in self.correlation_clusters.items():
            if symbol.upper() in members:
                return name
        return None


def load_config() -> Config:
    """Build Config from defaults, overlaying environment variables."""
    profile = os.environ.get("AXIOM_RISK_PROFILE", "").strip().upper()
    overrides: dict = {}
    if profile in RiskProfile.__members__:
        overrides["risk_profile"] = RiskProfile[profile]
    if db := os.environ.get("AXIOM_DB_PATH"):
        overrides["db_path"] = db
    if model := os.environ.get("AXIOM_LLM_MODEL"):
        overrides["llm_model"] = model
    base = Config()
    return replace(base, **overrides) if overrides else base


# Module-level default for convenience; callers may also build their own.
CONFIG = load_config()
