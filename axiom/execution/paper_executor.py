"""Paper executor — simulated fills only (spec §7 Phase 1 / §8 Step 8 PAPER).

Records orders + fills at the modeled net price, and OPEN/CLOSED positions, all
in the audit DB. There is intentionally NO live order code here; the live path
(review_option_order -> place_option_order) is Phase 3 and gated on explicit
operator approval.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from ..quant.payoff import Leg, Right, Side, economics, net_credit
from ..storage.db import Database, new_id, utcnow
from .manage import ManagedPosition


@dataclass(frozen=True)
class PaperFill:
    order_id: str
    position_id: str
    net_price: float          # per-contract net credit(+)/debit(-)
    contracts: int


def _short_strike(legs: list[Leg]) -> float | None:
    shorts = [leg.strike for leg in legs if leg.side is Side.SELL]
    return shorts[0] if shorts else None


def execute_paper(db: Database, decision_id: str, candidate, contracts: int,
                  max_loss_total: float) -> PaperFill:
    """Simulate an opening fill at the modeled net price and persist it.

    The position payload retains the legs + entry economics so the Step-9
    manager can mark and resolve it later (no re-derivation from prose).
    """
    order_id = new_id()
    position_id = new_id()
    net = net_credit(candidate.legs)  # dollars per contract (credit>0)
    econ = economics(candidate.legs)

    db.record_order({
        "order_id": order_id, "decision_id": decision_id, "mode": "PAPER",
        "symbol": candidate.symbol, "side": "OPEN", "status": "SIMULATED",
        "limit_price": round(net / 100.0, 4),
        "legs": [leg_to_dict(leg) for leg in candidate.legs],
    })
    db.record_fill({
        "order_id": order_id, "fill_price": round(net / 100.0, 4),
        "contracts": contracts, "fees": 0.0, "simulated": True,
    })
    payload = {
        "legs": [leg_to_dict(leg) for leg in candidate.legs],
        "entry_credit": round(net, 4),
        "max_profit": econ.max_profit,
        "max_loss": econ.max_loss,
        "dte": candidate.dte,
        "iv": candidate.iv,
        "short_strike": _short_strike(candidate.legs),
        "opened_spot": candidate.spot,
    }
    db.insert("positions", {
        "position_id": position_id, "decision_id": decision_id,
        "symbol": candidate.symbol, "structure_type": candidate.structure_type.value,
        "contracts": contracts, "opened_at": utcnow(), "closed_at": None,
        "status": "OPEN", "max_loss_total": max_loss_total,
        "net_delta": 0.0, "net_gamma": 0.0, "net_vega": 0.0, "net_theta": 0.0,
        "payload_json": json.dumps(payload),
    })
    return PaperFill(order_id, position_id, round(net / 100.0, 4), contracts)


def close_paper(db: Database, position_id: str, pnl_per_contract: float,
                reason: str, invalidation: bool = False) -> None:
    """Simulate a closing fill, mark the position CLOSED, and write the outcome.

    Reuses ``learning.ledger.resolve_outcome`` so the realized result joins the
    originating decision by key (spec §11 Layer 1).
    """
    from ..learning.ledger import resolve_outcome

    pos = db.query_one("SELECT * FROM positions WHERE position_id=?", (position_id,))
    if pos is None or pos["status"] != "OPEN":
        return
    contracts = pos["contracts"]
    realized = pnl_per_contract * contracts
    order_id = new_id()

    db.record_order({
        "order_id": order_id, "decision_id": pos["decision_id"], "mode": "PAPER",
        "symbol": pos["symbol"], "side": "CLOSE", "status": "SIMULATED",
        "limit_price": round(pnl_per_contract / 100.0, 4), "reason": reason,
    })
    db.record_fill({
        "order_id": order_id, "fill_price": round(pnl_per_contract / 100.0, 4),
        "contracts": contracts, "fees": 0.0, "simulated": True,
    })
    db.conn.execute(
        "UPDATE positions SET status='CLOSED', closed_at=? WHERE position_id=?",
        (utcnow(), position_id))
    db.conn.commit()

    # Modeled EV at decision time, for realized-vs-modeled.
    drow = db.query_one("SELECT ev_after_slippage, contracts FROM decisions WHERE decision_id=?",
                        (pos["decision_id"],))
    modeled_ev = (drow["ev_after_slippage"] or 0.0) * (drow["contracts"] or contracts) if drow else 0.0
    resolve_outcome(db, pos["decision_id"], realized_pnl=realized,
                    modeled_ev=modeled_ev, invalidation_triggered=invalidation)


def leg_to_dict(leg: Leg) -> dict:
    return {"side": leg.side.value, "right": leg.right.value,
            "strike": leg.strike, "premium": leg.premium}


def managed_from_row(row) -> ManagedPosition:
    """Reconstruct a ManagedPosition from a DB positions row's payload."""
    p = json.loads(row["payload_json"])
    legs = [
        Leg(Side(d["side"]), Right(d["right"]), d["strike"], d["premium"])
        for d in p["legs"]
    ]
    return ManagedPosition(
        position_id=row["position_id"], decision_id=row["decision_id"],
        symbol=row["symbol"], structure_type=row["structure_type"], legs=legs,
        entry_credit=p["entry_credit"], max_profit=p["max_profit"],
        max_loss=p["max_loss"], dte_remaining=p.get("dte", 0),
        short_strike=p.get("short_strike"),
    )
