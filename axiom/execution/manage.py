"""Position management — the decision-cycle close path (spec §4 Step 9, §5.5).

Deterministic exit discipline, each rule a config parameter validated by the
backtester / walk-forward meta-loop (spec §5.5, §2.6): profit target, stop,
time stop, and thesis invalidation. Marks are computed with the AXIOM
Black-Scholes model — no external mark feed is invented.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..config import ExitConfig
from ..quant.black_scholes import BSInputs, price
from ..quant.payoff import Leg, Right, Side, net_credit


class ManageAction(str, Enum):
    HOLD = "HOLD"
    EXIT = "EXIT"


@dataclass(frozen=True)
class ManagedPosition:
    position_id: str
    decision_id: str
    symbol: str
    structure_type: str
    legs: list[Leg]          # entry legs with entry premiums (per share)
    entry_credit: float      # net cash at open, dollars/contract (credit>0)
    max_profit: float        # dollars/contract
    max_loss: float          # dollars/contract (positive)
    dte_remaining: int
    short_strike: float | None = None  # nearest short strike, for invalidation

    @property
    def is_credit(self) -> bool:
        return self.entry_credit > 0


@dataclass(frozen=True)
class ManagementDecision:
    action: ManageAction
    reason: str
    pnl: float               # current realized-if-closed P&L, dollars/contract
    mark_value: float        # current net credit to re-enter, dollars/contract
    invalidation_triggered: bool = False


def _marked_legs(legs: list[Leg], spot: float, t: float, iv: float, rate: float) -> list[Leg]:
    """Copy legs with their premium replaced by the current BS mark."""
    out: list[Leg] = []
    for leg in legs:
        is_call = leg.right is Right.CALL
        mark = price(BSInputs(spot, leg.strike, max(t, 1e-6), rate, iv), is_call)
        out.append(Leg(side=leg.side, right=leg.right, strike=leg.strike, premium=mark))
    return out


def mark_value(legs: list[Leg], spot: float, t: float, iv: float, rate: float) -> float:
    """Current net credit (dollars/contract) to RE-ENTER the same legs now."""
    return net_credit(_marked_legs(legs, spot, t, iv, rate))


def position_pnl(pos: ManagedPosition, spot: float, t: float, iv: float, rate: float) -> float:
    """P&L per contract if closed now = cash received at open minus cost to close."""
    return pos.entry_credit - mark_value(pos.legs, spot, t, iv, rate)


def decide_management(
    pos: ManagedPosition, spot: float, t: float, iv: float, rate: float,
    cfg: ExitConfig,
) -> ManagementDecision:
    """Apply the §5.5 exit rules; return HOLD or EXIT with a reason."""
    mark = mark_value(pos.legs, spot, t, iv, rate)
    pnl = pos.entry_credit - mark

    # Thesis invalidation (spec §5.5): underlying through the short strike.
    invalidated = False
    if pos.short_strike is not None:
        if pos.structure_type.startswith("PUT") and spot < pos.short_strike:
            invalidated = pos.is_credit  # only credit sells use breach-of-short
        elif pos.structure_type.startswith("CALL") and spot > pos.short_strike:
            invalidated = pos.is_credit

    if pos.is_credit:
        if pnl >= cfg.profit_target_pct * pos.max_profit:
            return ManagementDecision(ManageAction.EXIT, "profit target", pnl, mark)
        if pnl <= -cfg.stop_mult_credit * pos.entry_credit:
            return ManagementDecision(ManageAction.EXIT, "stop (2x credit)", pnl, mark)
    else:
        debit = -pos.entry_credit
        if pnl >= cfg.debit_profit_target_pct * pos.max_profit:
            return ManagementDecision(ManageAction.EXIT, "debit profit target", pnl, mark)
        if pnl <= -cfg.debit_stop_pct * debit:
            return ManagementDecision(ManageAction.EXIT, "debit stop", pnl, mark)

    if pos.dte_remaining <= cfg.time_stop_dte:
        return ManagementDecision(ManageAction.EXIT, "time stop", pnl, mark)
    if invalidated:
        return ManagementDecision(ManageAction.EXIT, "thesis invalidation", pnl, mark,
                                  invalidation_triggered=True)
    return ManagementDecision(ManageAction.HOLD, "within thesis", pnl, mark)


def run_management(db, cfg, market_fn) -> list[dict]:
    """Decision-cycle Step 9: mark every OPEN position and apply exit rules.

    ``market_fn(pos) -> (spot, iv, t_remaining_years)`` supplies the current
    market; the live orchestrator builds it from the adapter, the simulator from
    its synthetic path. Closes positions that hit a rule via ``close_paper``.
    """
    from dataclasses import replace

    from .paper_executor import close_paper, managed_from_row

    closures: list[dict] = []
    for row in db.query("SELECT * FROM positions WHERE status='OPEN'"):
        pos = managed_from_row(row)
        spot, iv, t = market_fn(pos)
        pos = replace(pos, dte_remaining=max(0, int(round(t * 365))))
        decision = decide_management(pos, spot, max(t, 1e-6), iv,
                                     cfg.risk_free_rate, cfg.exit_rules)
        if decision.action is ManageAction.EXIT:
            close_paper(db, pos.position_id, decision.pnl, decision.reason,
                        decision.invalidation_triggered)
            closures.append({"position_id": pos.position_id, "symbol": pos.symbol,
                             "reason": decision.reason, "pnl": decision.pnl})
    return closures


def expiry_pnl(pos: ManagedPosition, terminal_spot: float) -> float:
    """Realized P&L per contract if held to expiry at ``terminal_spot``."""
    from ..quant.payoff import payoff_curve
    import numpy as np
    return float(payoff_curve(pos.legs, np.array([terminal_spot]))[0])
