"""SQLite storage / retention-contract tests (spec §7)."""

from axiom.storage.db import Database


def test_schema_tables_created(db: Database):
    rows = db.query("SELECT name FROM sqlite_master WHERE type='table'")
    names = {r["name"] for r in rows}
    for expected in {"decisions", "candidates", "orders", "fills", "positions",
                     "outcomes", "forecasts", "postmortems", "learning_state",
                     "metrics"}:
        assert expected in names


def test_decision_outcome_join_by_key(db: Database):
    decision = {
        "decision_id": "dec-1", "mode": "PAPER", "symbol": "SPY",
        "decision": "ENTER", "regime": "CHOP", "inputs_ref": "sha256:x",
        "features": {"iv_rank": 0.6, "dte_bucket": "22-45"},
        "modeled_economics": {"ev_after_slippage": 12.0, "max_loss": 300},
        "sizing": {"contracts": 1, "capital_at_risk": 300},
    }
    db.record_decision(decision)
    db.record_outcome({"decision_id": "dec-1", "realized_pnl": 50.0, "win": True,
                       "realized_vs_modeled": 38.0})
    row = db.query_one(
        "SELECT d.symbol, o.realized_pnl FROM decisions d "
        "JOIN outcomes o ON o.decision_id = d.decision_id WHERE d.decision_id=?",
        ("dec-1",))
    assert row["symbol"] == "SPY"
    assert row["realized_pnl"] == 50.0


def test_feature_columns_are_first_class(db: Database):
    db.record_decision({
        "decision_id": "dec-2", "mode": "SHADOW", "symbol": "NVDA",
        "decision": "PASS", "regime": "CHOP", "inputs_ref": "sha256:y",
        "features": {"iv_rank": 0.71, "sector_cluster": "ai_semis"},
    })
    row = db.query_one("SELECT iv_rank, sector_cluster FROM decisions WHERE decision_id=?",
                       ("dec-2",))
    assert row["iv_rank"] == 0.71
    assert row["sector_cluster"] == "ai_semis"


def test_learning_state_persists_and_upserts(db: Database):
    db.put_learning_state("k", {"a": 1})
    assert db.get_learning_state("k") == {"a": 1}
    db.put_learning_state("k", {"a": 2})
    assert db.get_learning_state("k") == {"a": 2}


def test_learning_state_survives_reopen(tmp_path):
    path = str(tmp_path / "persist.db")
    d1 = Database(path)
    d1.put_learning_state("calibration_map", {"slope": 0.9})
    d1.close()
    d2 = Database(path)
    assert d2.get_learning_state("calibration_map") == {"slope": 0.9}
    d2.close()
