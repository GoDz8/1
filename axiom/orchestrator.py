"""Decision-cycle orchestrator (spec §4 Steps 1-9), market-hours aware.

Drives one full cycle per symbol: regime -> filters -> gating -> EV candidates ->
LLM/stub selection + conviction -> sizing -> guard -> paper execute -> capture.
Fails closed everywhere: any missing datum or exception for a symbol yields a
logged PASS for that symbol, never a fabricated trade.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone

from .config import Config, resolve_tier
from .data.catalysts import CatalystSource, NullCatalystSource
from .data.robinhood_mcp import OptionChain, RobinhoodAdapter
from .execution.guard import KillSwitchState
from .execution.paper_executor import execute_paper
from .learning.ledger import capture_decision
from .logging_setup import get_logger
from .models import Decision, DecisionType, Features, Mode, Regime
from .quant.iv_rank import iv_rank as compute_iv_rank
from .quant.portfolio import OpenPosition, PortfolioState
from .quant.slippage import Quote
from .reasoning.decide import DecisionContext, StubReasoner, _pass_decision
from .regime import RegimeSnapshot, label_regime
from .storage.db import Database
from .strategy.candidates import build_candidates
from .strategy.gating import GatingInput, gate

_log = get_logger("orchestrator")

TARGET_DTE = 35


@dataclass
class CycleReport:
    tier: int
    decisions: list[Decision]


def is_market_hours(now: datetime | None = None) -> bool:
    """US equity options regular hours, 9:30-16:00 ET, Mon-Fri (approx, UTC-5)."""
    now = now or datetime.now(timezone.utc)
    if now.weekday() >= 5:
        return False
    # Convert UTC to a naive ET-ish check (ignores DST; conservative).
    et_hour = (now.hour - 5) % 24
    t = time(et_hour, now.minute)
    return time(9, 30) <= t <= time(16, 0)


def load_portfolio(db: Database, nlv: float) -> PortfolioState:
    """Reconstruct portfolio state from OPEN positions in the audit DB."""
    rows = db.query("SELECT * FROM positions WHERE status='OPEN'")
    positions = [
        OpenPosition(
            symbol=r["symbol"], structure_type=r["structure_type"],
            contracts=r["contracts"], max_loss_total=r["max_loss_total"] or 0.0,
            net_delta=r["net_delta"] or 0.0, net_gamma=r["net_gamma"] or 0.0,
            net_vega=r["net_vega"] or 0.0, net_theta=r["net_theta"] or 0.0,
        )
        for r in rows
    ]
    return PortfolioState(nlv=nlv, positions=positions)


def _liquidity_ok(chain: OptionChain, cfg: Config) -> tuple[bool, bool]:
    """Return (open_interest_ok, spread_ok) from the near-ATM quotes."""
    spot = chain.underlying.last
    near = sorted(chain.quotes, key=lambda q: abs(q.strike - spot))[:8]
    if not near:
        return False, False
    oi_ok = min(q.open_interest for q in near) >= cfg.gating.min_open_interest
    widths = [Quote(q.bid, q.ask).width_pct for q in near if q.bid > 0]
    spread_ok = bool(widths) and max(widths) <= cfg.gating.max_bid_ask_width_pct
    return oi_ok, spread_ok


def _proxy_iv_rank(db: Database, symbol: str, current_iv: float) -> float | None:
    """IV rank from our OWN rolling IV history (spec §6), persisted in learning_state.

    Appends today's ATM IV to the per-symbol history and computes the rank. Until
    enough observations accumulate this returns None (-> PASS, fail closed): real
    IV rank requires real history, and we never fabricate one.
    """
    key = f"iv_history:{symbol}"
    state = db.get_learning_state(key) or {"iv": []}
    history = state["iv"]
    rank = compute_iv_rank(current_iv, history)  # uses history BEFORE today
    history.append(round(current_iv, 6))
    db.put_learning_state(key, {"iv": history[-252:]})  # ~1y rolling window
    return rank


def _dte_bucket(dte: int) -> str:
    if dte <= 7:
        return "0-7"
    if dte <= 21:
        return "8-21"
    if dte <= 45:
        return "22-45"
    return "46+"


def process_symbol(db: Database, adapter: RobinhoodAdapter, cfg: Config,
                   symbol: str, mode: Mode, regime_override: Regime | None,
                   ks: KillSwitchState, catalysts: CatalystSource,
                   reasoner: StubReasoner) -> tuple[Decision, list]:
    """Run Steps 2-7 for a single symbol. Returns (decision, candidates)."""
    account = adapter.get_account()
    chain = adapter.get_option_chain(symbol, TARGET_DTE)
    if account is None or chain is None or not chain.quotes:
        ctx = _empty_ctx(symbol, mode, account, ks)
        return _pass_decision(ctx, "data unavailable (fail closed)"), []

    portfolio = load_portfolio(db, account.nlv)
    atm_iv = next((q.iv for q in chain.quotes if q.iv), None)
    if atm_iv is None:
        ctx = _empty_ctx(symbol, mode, account, ks, portfolio)
        return _pass_decision(ctx, "IV unavailable (fail closed)"), []

    iv_rank = _proxy_iv_rank(db, symbol, atm_iv)
    days_to_earnings = adapter.get_days_to_earnings(symbol)
    catalyst = catalysts.find_catalyst(symbol)
    oi_ok, spread_ok = _liquidity_ok(chain, cfg)

    # Regime (Step 1). Mock has no MA history; default CHOP unless event pending.
    snap = RegimeSnapshot(
        vix=ks.vix, price=chain.underlying.last, sma20=None, sma50=None,
        realized_vol=None, implied_vol=atm_iv,
        days_to_major_event=days_to_earnings,
    )
    regime = regime_override or label_regime(snap, cfg.kill_switch)

    gating = gate(GatingInput(symbol, iv_rank, days_to_earnings, catalyst,
                              oi_ok, spread_ok), cfg.gating)
    candidates = build_candidates(chain, gating.allowed, cfg, TARGET_DTE)

    features = Features(
        iv_rank=iv_rank, iv_percentile=None,
        sector_cluster=cfg.cluster_for(symbol), catalyst_type=(catalyst.kind if catalyst else None),
        dte_bucket=_dte_bucket(TARGET_DTE), days_to_earnings=days_to_earnings,
    )
    ctx = DecisionContext(
        symbol=symbol, mode=mode, regime=regime, account=account,
        portfolio=portfolio, candidates=candidates, features=features,
        days_to_earnings=days_to_earnings, kill_switch=ks,
    )
    if gating.is_empty:
        return _pass_decision(ctx, "gating empty: " + "; ".join(gating.reasons)), candidates

    decision = reasoner.decide(ctx, cfg)
    return decision, candidates


def _empty_ctx(symbol, mode, account, ks, portfolio=None) -> DecisionContext:
    from .data.robinhood_mcp import AccountSnapshot
    acct = account or AccountSnapshot(0.0, 0.0, 0)
    return DecisionContext(
        symbol=symbol, mode=mode, regime=Regime.CHOP, account=acct,
        portfolio=portfolio or PortfolioState(nlv=acct.nlv), candidates=[],
        features=Features(), days_to_earnings=None, kill_switch=ks,
    )


def run_cycle(db: Database, adapter: RobinhoodAdapter, cfg: Config,
              mode: Mode = Mode.PAPER, symbols=None,
              ks: KillSwitchState | None = None,
              catalysts: CatalystSource | None = None,
              reasoner: StubReasoner | None = None,
              regime_override: Regime | None = None) -> CycleReport:
    """Run one full decision cycle over the watchlist (spec §4)."""
    symbols = list(symbols or cfg.watchlist)
    ks = ks or KillSwitchState()
    catalysts = catalysts or NullCatalystSource()
    reasoner = reasoner or StubReasoner()

    account = adapter.get_account()
    nlv = account.nlv if account else 0.0
    tier = resolve_tier(nlv)
    _log.info("cycle start: mode=%s nlv=%.2f tier=%s positions_allowed=%d",
              mode.value, nlv, tier.tier.name, tier.max_concurrent_positions)
    db.record_metric("nlv", nlv)
    db.record_metric("tier", float(tier.tier.value))

    decisions: list[Decision] = []
    for symbol in symbols:
        try:
            decision, candidates = process_symbol(
                db, adapter, cfg, symbol, mode, regime_override, ks, catalysts, reasoner)
        except Exception as exc:  # noqa: BLE001 — fail closed to PASS
            _log.exception("symbol %s errored -> PASS: %s", symbol, exc)
            ctx = _empty_ctx(symbol, mode, account, ks)
            decision, candidates = _pass_decision(ctx, f"exception: {exc}"), []

        capture_decision(db, decision, candidates)

        # Tier-aware concurrency cap (spec §5.0): respect max concurrent positions.
        if decision.decision is DecisionType.ENTER and mode is Mode.PAPER:
            open_count = len(load_portfolio(db, nlv).positions)
            if open_count >= tier.max_concurrent_positions:
                _log.info("%s ENTER suppressed: tier position cap (%d) reached",
                          symbol, tier.max_concurrent_positions)
            else:
                fill = execute_paper(
                    db, decision.decision_id,
                    _candidate_for(candidates, decision),
                    decision.sizing.contracts,
                    decision.modeled_economics.max_loss * decision.sizing.contracts,
                )
                _log.info("%s PAPER fill: %d contracts @ net %.2f",
                          symbol, fill.contracts, fill.net_price)
        decisions.append(decision)

    _log.info("cycle complete: %d decisions (%d ENTER)", len(decisions),
              sum(1 for d in decisions if d.decision is DecisionType.ENTER))
    return CycleReport(tier=tier.tier.value, decisions=decisions)


def _candidate_for(candidates: list, decision: Decision):
    """Find the candidate matching the chosen decision's structure type."""
    for c in candidates:
        if c.accepted and c.structure_type.value == decision.structure.type.value:
            return c
    # Fallback: first accepted candidate (sizing/economics already on decision).
    return next(c for c in candidates if c.accepted)
