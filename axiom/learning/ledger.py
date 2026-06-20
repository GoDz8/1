"""Layer-1 forecast & outcome ledger (spec §11 Layer 1 — the fuel).

Persists every decision (ENTER, PASS, SHADOW), every evaluated candidate, any
standalone forecasts, and — on close — the realized outcome, all keyed by
decision_id so a thesis can be joined to its outcome by key (never prose parse).
"""

from __future__ import annotations

from ..models import Decision
from ..storage.db import Database
from ..strategy.candidates import Candidate


def capture_decision(db: Database, decision: Decision,
                     candidates: list[Candidate] | None = None) -> None:
    """Persist a decision and the candidate set it was chosen from."""
    payload = decision.model_dump(mode="json")
    db.record_decision(payload)
    for cand in candidates or []:
        db.record_candidate({
            "decision_id": decision.decision_id if cand.accepted else None,
            "symbol": cand.symbol,
            "structure_type": cand.structure_type.value,
            "iv_rank": None,
            "pop": cand.ev.pop,
            "ev_after_slippage": cand.ev.ev_after_slippage,
            "ev_per_dollar_risk": cand.ev.ev_per_dollar_risk,
            "rejected_reason": cand.rejected_reason,
        })


def capture_forecast(db: Database, symbol: str, statement: str,
                     predicted_prob: float, resolution_date: str,
                     decision_id: str | None = None) -> None:
    """Record a standalone prediction to be scored later (spec §11 Layer 1/2)."""
    db.record_forecast({
        "decision_id": decision_id,
        "symbol": symbol,
        "statement": statement,
        "predicted_prob": predicted_prob,
        "resolution_date": resolution_date,
    })


def resolve_outcome(db: Database, decision_id: str, realized_pnl: float,
                    modeled_ev: float, invalidation_triggered: bool = False) -> None:
    """Append the realized result for a closed decision (spec §11 Layer 1)."""
    db.record_outcome({
        "decision_id": decision_id,
        "realized_pnl": realized_pnl,
        "win": realized_pnl > 0,
        "invalidation_triggered": invalidation_triggered,
        "realized_vs_modeled": realized_pnl - modeled_ev,
    })
