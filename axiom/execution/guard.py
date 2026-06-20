"""Pre-trade guard + kill switches (spec §5.3, §5.4, §5.6).

This is the mechanical, inviolable risk layer. It re-derives every risk check
deterministically from the candidate economics + portfolio/account state — it
does NOT trust the flags the LLM emitted (defense in depth, spec §5.6). If any
check fails, the trade is forced to PASS. The LLM can never reach past this.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import Config
from ..data.robinhood_mcp import AccountSnapshot
from ..models import RiskChecks
from ..quant.greeks import Greeks
from ..quant.payoff import StructureType
from ..quant.portfolio import (
    PortfolioState, correlation_ok_after, greeks_bands_ok_after, heat_ok_after,
)
from ..strategy.candidates import Candidate


# Structures that BUY net premium — subject to the earnings-buy block (§2.3).
_DEBIT_STRUCTURES = {
    StructureType.PUT_DEBIT_SPREAD, StructureType.CALL_DEBIT_SPREAD,
}


@dataclass(frozen=True)
class KillSwitchState:
    daily_loss_pct: float = 0.0           # realized today (positive = loss)
    drawdown_from_hwm: float = 0.0        # positive fraction
    consecutive_losses: int = 0
    vix: float | None = None
    data_feed_healthy: bool = True
    manual_halt: bool = False


@dataclass(frozen=True)
class GuardResult:
    approved: bool
    checks: RiskChecks
    reasons: tuple[str, ...]


def kill_switch_tripped(ks: KillSwitchState, cfg: Config) -> tuple[bool, str | None]:
    """Return (tripped, reason). Tripped => halt all new entries (spec §5.4)."""
    c = cfg.kill_switch
    if ks.manual_halt:
        return True, "manual halt engaged"
    if not ks.data_feed_healthy:
        return True, "data feed stale/disconnected"
    if ks.daily_loss_pct >= c.max_daily_loss:
        return True, f"daily loss {ks.daily_loss_pct:.2%} >= {c.max_daily_loss:.2%}"
    if ks.drawdown_from_hwm >= c.max_drawdown_from_hwm:
        return True, f"drawdown {ks.drawdown_from_hwm:.2%} >= {c.max_drawdown_from_hwm:.2%}"
    if ks.consecutive_losses >= c.max_consecutive_losses:
        return True, f"{ks.consecutive_losses} consecutive losses"
    if ks.vix is not None and ks.vix >= c.vix_spike_threshold:
        return True, f"VIX {ks.vix} >= spike threshold {c.vix_spike_threshold}"
    return False, None


def evaluate(
    candidate: Candidate,
    contracts: int,
    account: AccountSnapshot,
    portfolio: PortfolioState,
    days_to_earnings: int | None,
    ks: KillSwitchState,
    cfg: Config,
    position_greeks: Greeks | None = None,
) -> GuardResult:
    """Run the full §5.3 pre-trade checklist. All must pass to approve."""
    reasons: list[str] = []
    ev = candidate.ev
    max_loss_total = ev.max_loss * max(contracts, 0)

    # Kill switch (spec §5.4) — gate before anything else.
    tripped, ks_reason = kill_switch_tripped(ks, cfg)
    kill_switch_ok = not tripped
    if tripped:
        reasons.append(f"kill switch: {ks_reason}")

    # Buying power.
    buying_power_ok = account.buying_power >= max_loss_total > 0
    if not buying_power_ok:
        reasons.append("insufficient buying power")

    # Per-trade cap (spec §5.1) — inviolable.
    cap_dollars = cfg.per_trade_cap * account.nlv
    per_trade_cap_ok = 0 < max_loss_total <= cap_dollars + 1e-6
    if not per_trade_cap_ok:
        reasons.append(f"per-trade cap: max loss {max_loss_total:.2f} vs cap {cap_dollars:.2f}")

    # Portfolio heat (spec §5.2).
    portfolio_heat_ok = heat_ok_after(portfolio, max_loss_total, cfg)
    if not portfolio_heat_ok:
        reasons.append("portfolio heat cap breached")

    # Correlation cluster (spec §5.2).
    correlation_cap_ok = correlation_ok_after(portfolio, candidate.symbol, max_loss_total, cfg)
    if not correlation_cap_ok:
        reasons.append("correlation cluster cap breached")

    # Greeks bands (spec §5.2).
    add_greeks = position_greeks or Greeks(0, 0, 0, 0, 0)
    greeks_bands_ok = greeks_bands_ok_after(portfolio, add_greeks, cfg)
    if not greeks_bands_ok:
        reasons.append("Greeks bands breached")

    # Earnings-buy block (spec §2.3) — only debit structures are restricted.
    is_debit = candidate.structure_type in _DEBIT_STRUCTURES
    earnings_block = (days_to_earnings is not None
                      and days_to_earnings <= cfg.gating.earnings_block_days)
    earnings_rule_ok = not (is_debit and earnings_block)
    if not earnings_rule_ok:
        reasons.append("earnings-buy block: cannot buy premium into earnings")

    # PDT (spec §10) — under threshold, day trades are scarce. We model entries
    # as multi-day holds, so a new ENTER does not itself consume a day trade; the
    # check guards that we are not already over budget.
    pdt_ok = (account.nlv >= cfg.pdt.pdt_equity_threshold
              or account.day_trades_used < cfg.pdt.max_day_trades_per_5_business_days)
    if not pdt_ok:
        reasons.append("PDT budget exhausted")

    # Liquidity + EV gate already applied at candidate build; reflect it.
    liquidity_ok = candidate.accepted
    if not liquidity_ok:
        reasons.append(f"candidate rejected at gate: {candidate.rejected_reason}")

    checks = RiskChecks(
        buying_power_ok=buying_power_ok,
        per_trade_cap_ok=per_trade_cap_ok,
        portfolio_heat_ok=portfolio_heat_ok,
        correlation_cap_ok=correlation_cap_ok,
        greeks_bands_ok=greeks_bands_ok,
        earnings_rule_ok=earnings_rule_ok,
        pdt_ok=pdt_ok,
        liquidity_ok=liquidity_ok,
        kill_switch_ok=kill_switch_ok,
    )
    return GuardResult(approved=checks.all_pass(), checks=checks, reasons=tuple(reasons))
