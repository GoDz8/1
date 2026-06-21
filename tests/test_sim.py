"""Phase-2 simulator tests: the close path and the end-to-end learning loop."""

from axiom.config import load_config
from axiom.execution.paper_executor import execute_paper
from axiom.sim import SimAdapter, _manage_sim_positions, run_simulation
from axiom.strategy.candidates import build_candidates
from axiom.strategy.gating import StrategyFamily

CFG = load_config()


def test_sim_adapter_paths_are_deterministic():
    a = SimAdapter(CFG, ["NVDA"], 5000, 60, seed=1)
    b = SimAdapter(CFG, ["NVDA"], 5000, 60, seed=1)
    assert a.spot_at("NVDA", 30) == b.spot_at("NVDA", 30)
    assert a.iv_at("NVDA", 30) == b.iv_at("NVDA", 30)


def test_close_path_expires_position_and_records_outcome(db):
    ad = SimAdapter(CFG, ["NVDA"], 5000, 60, seed=1)
    ad.set_day(0)
    chain = ad.get_option_chain("NVDA", 35)
    cands = [c for c in build_candidates(chain, (StrategyFamily.CREDIT_SPREAD,), CFG, 35)
             if c.accepted]
    assert cands, "sim chain should yield an EV-positive credit spread"
    cand = cands[0]

    db.record_decision({
        "decision_id": "dec-sim", "mode": "PAPER", "symbol": "NVDA",
        "decision": "ENTER", "regime": "CHOP", "inputs_ref": "sha256:x",
        "modeled_economics": {"ev_after_slippage": 10.0}, "sizing": {"contracts": 1},
    })
    fill = execute_paper(db, "dec-sim", cand, 1, cand.ev.max_loss)
    entry_days = {fill.position_id: 0}

    # Step to a day past expiry (entry DTE 35) -> position must expire and close.
    closes, wins, realized = _manage_sim_positions(db, ad, CFG, 40, entry_days)
    assert closes == 1
    pos = db.query_one("SELECT status FROM positions WHERE position_id=?", (fill.position_id,))
    assert pos["status"] == "CLOSED"
    out = db.query_one("SELECT * FROM outcomes WHERE decision_id='dec-sim'")
    assert out is not None
    # realized_vs_modeled is recorded for calibration (spec §11 Layer 1).
    assert out["realized_vs_modeled"] is not None


def test_run_simulation_completes_and_persists_learning(db):
    rep = run_simulation(db, CFG, symbols=["NVDA", "AAPL"], nlv=5000,
                         horizon_days=60, seed=3, learn_every=0)
    assert rep.cycles == 61
    # The final learning refresh must persist a calibration map (identity-ish
    # until enough sample, but present and reconstructable).
    assert db.get_learning_state("calibration_map") is not None
    # Every captured decision is schema-valid and logged.
    assert db.query_one("SELECT COUNT(*) c FROM decisions")["c"] > 0
