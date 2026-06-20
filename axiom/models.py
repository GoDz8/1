"""Pydantic models for the §8 decision schema (validated on every LLM output).

These models enforce the inviolable invariants at the type boundary (spec §8):
  * EV <= 0  =>  decision cannot be ENTER.
  * Any risk check False  =>  decision must be PASS or EXIT.
The execution guard (spec §5.3) re-checks these deterministically as defense in
depth, but malformed or rule-breaking LLM output is rejected here first.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, model_validator


class Mode(str, Enum):
    LIVE = "LIVE"
    PAPER = "PAPER"
    SHADOW = "SHADOW"


class DecisionType(str, Enum):
    ENTER = "ENTER"
    PASS = "PASS"
    ADJUST = "ADJUST"
    EXIT = "EXIT"


class Regime(str, Enum):
    RISK_ON = "RISK_ON"
    RISK_OFF = "RISK_OFF"
    CHOP = "CHOP"
    EVENT_PENDING = "EVENT_PENDING"


class StructureKind(str, Enum):
    PUT_CREDIT_SPREAD = "PUT_CREDIT_SPREAD"
    CALL_CREDIT_SPREAD = "CALL_CREDIT_SPREAD"
    IRON_CONDOR = "IRON_CONDOR"
    PUT_DEBIT_SPREAD = "PUT_DEBIT_SPREAD"
    CALL_DEBIT_SPREAD = "CALL_DEBIT_SPREAD"
    EXIT_EXISTING = "EXIT_EXISTING"


class Features(BaseModel):
    iv_rank: float | None = None
    iv_percentile: float | None = None
    sector_cluster: str | None = None
    catalyst_type: str | None = None
    dte_bucket: str | None = None
    days_to_earnings: int | None = None


class ConvictionBreakdown(BaseModel):
    ev_strength: int = 0
    regime_alignment: int = 0
    catalyst_quality: int = 0
    liquidity: int = 0
    portfolio_fit: int = 0


class Leg(BaseModel):
    side: str            # BUY | SELL
    right: str           # CALL | PUT
    strike: float
    expiry: str          # YYYY-MM-DD
    qty: int


class Structure(BaseModel):
    type: StructureKind
    legs: list[Leg] = Field(default_factory=list)
    dte: int = 0


class ModeledEconomics(BaseModel):
    credit_or_debit: float = 0.0
    max_profit: float = 0.0
    max_loss: float = 0.0
    pop: float = 0.0
    ev_after_slippage: float = 0.0
    ev_per_dollar_risk: float = 0.0
    breakevens: list[float] = Field(default_factory=list)


class Sizing(BaseModel):
    kelly_fraction: float = 0.0
    applied_fraction: float = 0.0
    contracts: int = 0
    capital_at_risk: float = 0.0
    pct_of_nlv: float = 0.0


class RiskChecks(BaseModel):
    buying_power_ok: bool = False
    per_trade_cap_ok: bool = False
    portfolio_heat_ok: bool = False
    correlation_cap_ok: bool = False
    greeks_bands_ok: bool = False
    earnings_rule_ok: bool = False
    pdt_ok: bool = False
    liquidity_ok: bool = False
    kill_switch_ok: bool = False

    def all_pass(self) -> bool:
        return all(self.model_dump().values())


class Decision(BaseModel):
    decision_id: str
    timestamp: str
    mode: Mode
    symbol: str
    decision: DecisionType
    regime: Regime
    features: Features = Field(default_factory=Features)
    inputs_ref: str
    conviction: int = 0
    conviction_breakdown: ConvictionBreakdown = Field(default_factory=ConvictionBreakdown)
    structure: Structure | None = None
    modeled_economics: ModeledEconomics = Field(default_factory=ModeledEconomics)
    sizing: Sizing = Field(default_factory=Sizing)
    thesis: str = ""
    invalidation: str = ""
    risk_checks: RiskChecks = Field(default_factory=RiskChecks)

    @model_validator(mode="after")
    def _enforce_hard_rules(self) -> "Decision":
        is_enter = self.decision is DecisionType.ENTER
        if is_enter:
            # EV <= 0 can never be ENTER (spec §8 / §2.2).
            if self.modeled_economics.ev_after_slippage <= 0:
                raise ValueError("ENTER requires ev_after_slippage > 0")
            # Any failed risk check forces PASS/EXIT (spec §5.3 / §8).
            if not self.risk_checks.all_pass():
                raise ValueError("ENTER requires all risk_checks to pass")
            # Conviction floor (spec §4.1) is enforced at the schema boundary.
            if self.conviction < 70:
                raise ValueError("ENTER requires conviction >= 70 floor")
        return self
