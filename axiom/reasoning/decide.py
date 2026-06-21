"""Decision assembly (spec §4 Steps 5-7).

Bridges the generative selection step and the deterministic sizing/guard steps.
A ``StubReasoner`` selects the best EV-positive candidate and scores conviction
deterministically so the whole cycle runs offline; an ``AnthropicReasoner`` can
be swapped in for live reasoning. Either way the output is a §8 Decision that is
pydantic-validated and re-checked by the guard before it can become an order.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, timedelta

from ..config import Config
from ..data.robinhood_mcp import AccountSnapshot
from ..execution.guard import KillSwitchState, evaluate
from ..models import (
    ConvictionBreakdown, Decision, DecisionType, Features, Mode,
    ModeledEconomics, Regime, Sizing, Structure, StructureKind,
)
from ..quant.portfolio import PortfolioState
from ..quant.payoff import StructureType
from ..quant.sizing import size_position
from ..storage.db import new_id, utcnow
from ..strategy.candidates import Candidate

_CREDIT_TYPES = {
    StructureType.PUT_CREDIT_SPREAD, StructureType.CALL_CREDIT_SPREAD,
    StructureType.IRON_CONDOR,
}


@dataclass(frozen=True)
class DecisionContext:
    symbol: str
    mode: Mode
    regime: Regime
    account: AccountSnapshot
    portfolio: PortfolioState
    candidates: list[Candidate]
    features: Features
    days_to_earnings: int | None
    kill_switch: KillSwitchState
    # Continuous-learning signals (spec §11). Sample-gated; default to no-ops so
    # the system behaves identically to Phase 1 until a real sample accumulates.
    calibration: "object | None" = None      # CalibrationMap; .apply(p)->p'
    suppression: "object | None" = None      # SuppressionMap; .is_suppressed(...)
    past_cases: list = field(default_factory=list)  # list[PastCase] for context


def inputs_ref(ctx: DecisionContext) -> str:
    """Stable hash of the inputs this decision is made from (spec §0 reconstructability)."""
    snap = {
        "symbol": ctx.symbol,
        "regime": ctx.regime.value,
        "nlv": ctx.account.nlv,
        "features": ctx.features.model_dump(),
        "candidates": [
            {"type": c.structure_type.value, "ev": c.ev.ev_after_slippage,
             "pop": c.ev.pop, "epd": c.ev.ev_per_dollar_risk,
             "rejected": c.rejected_reason}
            for c in ctx.candidates
        ],
    }
    blob = json.dumps(snap, sort_keys=True, default=str)
    return "sha256:" + hashlib.sha256(blob.encode()).hexdigest()[:16]


def score_conviction(cand: Candidate, ctx: DecisionContext, cfg: Config) -> ConvictionBreakdown:
    """Deterministic conviction components (spec §4.1). The stub reasoner's honest score."""
    ev = cand.ev
    # EV strength (35): scaled by EV per dollar of risk.
    ev_strength = int(min(35, max(0, ev.ev_per_dollar_risk / 0.10 * 35)))

    is_credit = cand.structure_type in _CREDIT_TYPES
    # Regime alignment (20): credit sells love CHOP; debit wants a trend.
    if is_credit:
        regime_alignment = 20 if ctx.regime in (Regime.CHOP, Regime.RISK_ON) else 14
    else:
        regime_alignment = 18 if ctx.regime in (Regime.RISK_ON, Regime.RISK_OFF) else 8

    # Catalyst quality (15): sells -> elevated IV rank; debit -> catalyst given.
    ivr = ctx.features.iv_rank or 0.0
    if is_credit:
        catalyst_quality = int(min(15, max(0, (ivr - 0.5) / 0.5 * 15)))
    else:
        catalyst_quality = 12 if ctx.features.catalyst_type else 0

    # Liquidity (15): candidate passed the gate (OI/spread/EV) -> full marks.
    liquidity = 15 if cand.accepted else 0

    # Portfolio fit (15): scaled by remaining correlation-cluster room.
    cluster = cfg.cluster_for(cand.symbol)
    if cluster is None:
        portfolio_fit = 15
    else:
        used = ctx.portfolio.cluster_heat_dollars(cluster, cfg)
        cap = cfg.portfolio.max_correlation_cluster * max(ctx.account.nlv, 1e-9)
        room = max(0.0, 1.0 - used / cap) if cap > 0 else 0.0
        portfolio_fit = int(round(room * 15))

    return ConvictionBreakdown(
        ev_strength=ev_strength,
        regime_alignment=regime_alignment,
        catalyst_quality=catalyst_quality,
        liquidity=liquidity,
        portfolio_fit=portfolio_fit,
    )


def _structure_payload(cand: Candidate) -> Structure:
    expiry = (date.today() + timedelta(days=cand.dte)).isoformat()
    legs = [
        {"side": leg.side.value, "right": leg.right.value, "strike": leg.strike,
         "expiry": expiry, "qty": 1}
        for leg in cand.legs
    ]
    return Structure(type=StructureKind(cand.structure_type.value), legs=legs, dte=cand.dte)


def _pass_decision(ctx: DecisionContext, reason: str,
                   conviction: int = 0,
                   breakdown: ConvictionBreakdown | None = None) -> Decision:
    return Decision(
        decision_id=new_id(),
        timestamp=utcnow(),
        mode=ctx.mode,
        symbol=ctx.symbol,
        decision=DecisionType.PASS,
        regime=ctx.regime,
        features=ctx.features,
        inputs_ref=inputs_ref(ctx),
        conviction=conviction,
        conviction_breakdown=breakdown or ConvictionBreakdown(),
        thesis=f"PASS: {reason}",
        invalidation="n/a",
    )


def _memory_note(past_cases: list) -> str:
    """Summarize retrieved similar past cases for the decision thesis (Layer 4)."""
    if not past_cases:
        return ""
    wins = sum(1 for c in past_cases if getattr(c, "win", False))
    return f" [memory: {wins}/{len(past_cases)} similar prior cases won]"


class StubReasoner:
    """Deterministic selection + conviction (no API key required)."""

    def decide(self, ctx: DecisionContext, cfg: Config) -> Decision:
        accepted = [c for c in ctx.candidates if c.accepted]
        # Adaptive gating (spec §11 Layer 3): drop buckets with statistically
        # significant negative realized expectancy. No-op until a sample exists.
        if ctx.suppression is not None:
            accepted = [
                c for c in accepted
                if not ctx.suppression.is_suppressed(
                    c.structure_type.value, ctx.regime.value, ctx.features.iv_rank)
            ]
        if not accepted:
            return _pass_decision(ctx, "no EV-positive candidate qualified")

        # Select the highest expectancy-per-risk candidate (spec §4.4).
        best = max(accepted, key=lambda c: c.ev.ev_per_dollar_risk)
        # Calibration (spec §11 Layer 2): correct POP for historical over/under-
        # confidence before it feeds sizing. Identity until N>=min_resolved.
        cal_pop = (ctx.calibration.apply(best.ev.pop)
                   if ctx.calibration is not None else best.ev.pop)
        breakdown = score_conviction(best, ctx, cfg)
        conviction = (breakdown.ev_strength + breakdown.regime_alignment
                      + breakdown.catalyst_quality + breakdown.liquidity
                      + breakdown.portfolio_fit)

        if conviction < cfg.sizing.conviction_floor:
            return _pass_decision(ctx, f"conviction {conviction} below floor", conviction, breakdown)

        # Deterministic sizing (spec §5.1). Uses the CALIBRATED POP so a
        # historically overconfident model sizes smaller — learning can only
        # shrink risk here, never raise it past the §5.6 caps.
        sizing = size_position(
            pop=cal_pop, max_profit=best.ev.max_profit, max_loss=best.ev.max_loss,
            conviction=conviction, nlv=ctx.account.nlv,
            per_trade_cap=cfg.per_trade_cap,
            remaining_heat_dollars=ctx.portfolio.remaining_heat_dollars(cfg),
            cfg=cfg.sizing,
        )
        if sizing.contracts <= 0:
            return _pass_decision(ctx, f"sizing -> 0 contracts ({sizing.binding_constraint})",
                                  conviction, breakdown)

        max_loss_total = best.ev.max_loss * sizing.contracts

        # Pre-trade guard (spec §5.3) — re-validates everything mechanically.
        guard = evaluate(
            candidate=best, contracts=sizing.contracts, account=ctx.account,
            portfolio=ctx.portfolio, days_to_earnings=ctx.days_to_earnings,
            ks=ctx.kill_switch, cfg=cfg,
        )
        if not guard.approved:
            d = _pass_decision(ctx, "guard rejected: " + "; ".join(guard.reasons),
                               conviction, breakdown)
            return d.model_copy(update={"risk_checks": guard.checks})

        econ = ModeledEconomics(
            credit_or_debit=round(best.ev.max_profit if best.structure_type in _CREDIT_TYPES
                                  else -best.ev.max_loss, 2),
            max_profit=best.ev.max_profit,
            max_loss=best.ev.max_loss,
            pop=best.ev.pop,
            ev_after_slippage=best.ev.ev_after_slippage,
            ev_per_dollar_risk=best.ev.ev_per_dollar_risk,
            breakevens=list(best.ev.breakevens),
        )
        return Decision(
            decision_id=new_id(),
            timestamp=utcnow(),
            mode=ctx.mode,
            symbol=ctx.symbol,
            decision=DecisionType.ENTER,
            regime=ctx.regime,
            features=ctx.features,
            inputs_ref=inputs_ref(ctx),
            conviction=conviction,
            conviction_breakdown=breakdown,
            structure=_structure_payload(best),
            modeled_economics=econ,
            sizing=Sizing(
                kelly_fraction=sizing.kelly_fraction,
                applied_fraction=sizing.applied_fraction,
                contracts=sizing.contracts,
                capital_at_risk=sizing.capital_at_risk,
                pct_of_nlv=sizing.pct_of_nlv,
            ),
            thesis=(f"{best.structure_type.value} on {ctx.symbol}: IV-rank "
                    f"{ctx.features.iv_rank}, POP {best.ev.pop:.2f} (cal {cal_pop:.2f}), "
                    f"EV/risk {best.ev.ev_per_dollar_risk:.3f} after slippage in "
                    f"{ctx.regime.value}." + _memory_note(ctx.past_cases)),
            invalidation=("Underlying breaches the short strike with trend confirmation, "
                          "or IV rank collapses below 30 (edge gone)."),
            risk_checks=guard.checks,
        )
