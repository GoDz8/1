"""Post-mortem tests (spec §11 Layer 5): process vs outcome, anti-resulting."""

from axiom.learning.postmortem import (
    FLAWED_LOST, GOOD_LOST, GOOD_WON, classify, write_postmortem,
)
from axiom.storage.db import new_id, utcnow


def test_classify_grid():
    assert classify(True, True) == GOOD_WON
    assert classify(True, False) == GOOD_LOST
    assert classify(False, True) == "flawed_process_won"
    assert classify(False, False) == FLAWED_LOST


def _enter(db, conviction, ev, pnl, win):
    did = new_id()
    db.record_decision({
        "decision_id": did, "mode": "PAPER", "symbol": "SPY",
        "decision": "ENTER", "regime": "CHOP", "inputs_ref": "sha256:x",
        "conviction": conviction,
        "modeled_economics": {"ev_after_slippage": ev},
    })
    db.record_outcome({"decision_id": did, "realized_pnl": pnl, "win": win,
                       "resolved_at": utcnow()})
    return did


def test_good_process_loss_not_punished(db):
    # EV+, conviction above floor, but lost -> GOOD_LOST = variance, no blame.
    did = _enter(db, conviction=80, ev=12.0, pnl=-300.0, win=False)
    pm = write_postmortem(db, did)
    assert pm.quadrant == GOOD_LOST
    assert "variance" in pm.lesson.lower()


def test_good_process_win(db):
    did = _enter(db, conviction=82, ev=15.0, pnl=120.0, win=True)
    pm = write_postmortem(db, did)
    assert pm.quadrant == GOOD_WON


def test_process_judged_on_decision_not_result(db):
    # Same losing outcome, but conviction below floor -> flawed process.
    did = _enter(db, conviction=55, ev=5.0, pnl=-100.0, win=False)
    pm = write_postmortem(db, did)
    assert pm.quadrant == FLAWED_LOST
    assert "below_conviction_floor" in pm.error_flags


def test_postmortem_persisted(db):
    did = _enter(db, conviction=80, ev=12.0, pnl=50.0, win=True)
    write_postmortem(db, did)
    row = db.query_one("SELECT quadrant FROM postmortems WHERE decision_id=?", (did,))
    assert row["quadrant"] == GOOD_WON
