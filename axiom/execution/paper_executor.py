"""Paper executor — simulated fills only (spec §7 Phase 1 / §8 Step 8 PAPER).

Records an order + a fill at the modeled net price, and an OPEN position, all in
the audit DB. There is intentionally NO live order code here; the live path
(review_option_order -> place_option_order) is Phase 3 and gated on explicit
operator approval.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..quant.payoff import Side, net_credit
from ..storage.db import Database, new_id, utcnow
from ..strategy.candidates import Candidate


@dataclass(frozen=True)
class PaperFill:
    order_id: str
    position_id: str
    net_price: float          # per-contract net credit(+)/debit(-)
    contracts: int


def execute_paper(db: Database, decision_id: str, candidate: Candidate,
                  contracts: int, max_loss_total: float) -> PaperFill:
    """Simulate an opening fill at the modeled net price and persist it."""
    order_id = new_id()
    position_id = new_id()
    net = net_credit(candidate.legs)  # dollars per contract (credit>0)
    side = "OPEN"

    db.record_order({
        "order_id": order_id,
        "decision_id": decision_id,
        "mode": "PAPER",
        "symbol": candidate.symbol,
        "side": side,
        "status": "SIMULATED",
        "limit_price": round(net / 100.0, 4),
        "legs": [leg.__dict__ for leg in candidate.legs],
    })
    db.record_fill({
        "order_id": order_id,
        "fill_price": round(net / 100.0, 4),
        "contracts": contracts,
        "fees": 0.0,
        "simulated": True,
    })
    db.insert("positions", {
        "position_id": position_id,
        "decision_id": decision_id,
        "symbol": candidate.symbol,
        "structure_type": candidate.structure_type.value,
        "contracts": contracts,
        "opened_at": utcnow(),
        "closed_at": None,
        "status": "OPEN",
        "max_loss_total": max_loss_total,
        "net_delta": 0.0,
        "net_gamma": 0.0,
        "net_vega": 0.0,
        "net_theta": 0.0,
        "payload_json": "{}",
    })
    return PaperFill(order_id, position_id, round(net / 100.0, 4), contracts)
