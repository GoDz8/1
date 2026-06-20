"""Layer-1 ledger capture tests (spec §11 Layer 1)."""

from axiom.learning.ledger import capture_decision, capture_forecast, resolve_outcome
from axiom.models import Decision, DecisionType, Mode, Regime


def _pass_decision(symbol="SPY"):
    return Decision(
        decision_id=f"d-{symbol}", timestamp="2026-06-20T00:00:00Z",
        mode=Mode.SHADOW, symbol=symbol, decision=DecisionType.PASS,
        regime=Regime.CHOP, inputs_ref="sha256:abc", thesis="no edge",
    )


def test_capture_pass_decision(db):
    capture_decision(db, _pass_decision("SPY"))
    row = db.query_one("SELECT * FROM decisions WHERE decision_id='d-SPY'")
    assert row["decision"] == "PASS"
    assert row["mode"] == "SHADOW"  # shadow/pass are captured for calibration


def test_capture_forecast_and_resolve(db):
    capture_decision(db, _pass_decision("NVDA"))
    capture_forecast(db, "NVDA", "NVDA holds 100 through Friday", 0.7,
                     "2026-06-27", decision_id="d-NVDA")
    f = db.query_one("SELECT * FROM forecasts WHERE symbol='NVDA'")
    assert f["predicted_prob"] == 0.7
    assert f["resolved"] == 0


def test_resolve_outcome_links_by_key(db):
    capture_decision(db, _pass_decision("AMD"))
    resolve_outcome(db, "d-AMD", realized_pnl=-50.0, modeled_ev=10.0)
    o = db.query_one("SELECT * FROM outcomes WHERE decision_id='d-AMD'")
    assert o["win"] == 0
    assert o["realized_vs_modeled"] == -60.0
