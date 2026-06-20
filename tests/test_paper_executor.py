"""Paper executor tests (simulated fills only)."""

from axiom.execution.paper_executor import execute_paper
from axiom.quant.ev import EVResult
from axiom.quant.payoff import Leg, Right, Side, StructureType
from axiom.strategy.candidates import Candidate
from axiom.strategy.gating import StrategyFamily


def _candidate():
    ev = EVResult(30, 25, 0.7, 100, 400, (94.0,), 0.0625, 0.0625)
    legs = [Leg(Side.SELL, Right.PUT, 95, 2.0), Leg(Side.BUY, Right.PUT, 90, 1.0)]
    return Candidate("SPY", StructureType.PUT_CREDIT_SPREAD,
                     StrategyFamily.CREDIT_SPREAD, legs, 35, 100.0, 0.3, ev)


def test_execute_paper_writes_order_fill_position(db):
    # A parent decision row must exist (FK chain).
    db.record_decision({
        "decision_id": "dec-x", "mode": "PAPER", "symbol": "SPY",
        "decision": "ENTER", "regime": "CHOP", "inputs_ref": "sha256:z",
    })
    fill = execute_paper(db, "dec-x", _candidate(), contracts=2, max_loss_total=800)
    assert fill.contracts == 2

    order = db.query_one("SELECT * FROM orders WHERE decision_id=?", ("dec-x",))
    assert order["status"] == "SIMULATED"
    f = db.query_one("SELECT * FROM fills WHERE order_id=?", (fill.order_id,))
    assert f["simulated"] == 1 and f["contracts"] == 2
    pos = db.query_one("SELECT * FROM positions WHERE decision_id=?", ("dec-x",))
    assert pos["status"] == "OPEN" and pos["contracts"] == 2
