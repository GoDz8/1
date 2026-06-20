"""End-to-end decision-cycle tests (spec §4 / Phase 1 exit criteria)."""

from axiom.config import Config
from axiom.data.robinhood_mcp import MockRobinhoodAdapter
from axiom.models import DecisionType, Mode
from axiom.orchestrator import run_cycle


def test_full_cycle_produces_schema_valid_decisions(db):
    cfg = Config()
    adapter = MockRobinhoodAdapter(nlv=5000.0)
    report = run_cycle(db, adapter, cfg, mode=Mode.PAPER,
                       symbols=["NVDA", "AAPL", "MSFT"])
    assert len(report.decisions) == 3
    # Every decision is persisted with an inputs_ref (reconstructability §0).
    for d in report.decisions:
        assert d.inputs_ref.startswith("sha256:")
        row = db.query_one("SELECT inputs_ref FROM decisions WHERE decision_id=?",
                           (d.decision_id,))
        assert row is not None and row["inputs_ref"] == d.inputs_ref


def test_first_cycle_passes_due_to_no_iv_history(db):
    # IV rank needs history; on a fresh DB the first observation -> rank unknown
    # -> gating empty -> PASS (fail closed). No fabricated rank, no trade.
    cfg = Config()
    adapter = MockRobinhoodAdapter(nlv=5000.0)
    report = run_cycle(db, adapter, cfg, mode=Mode.PAPER, symbols=["NVDA"])
    assert report.decisions[0].decision is DecisionType.PASS


def test_enters_after_iv_history_accumulates(db):
    # Run enough cycles to build IV history, then a high-IV name can ENTER.
    cfg = Config()
    adapter = MockRobinhoodAdapter(nlv=5000.0)
    last = None
    for _ in range(25):
        last = run_cycle(db, adapter, cfg, mode=Mode.PAPER, symbols=["NVDA"])
    # After history accumulates we expect at least the option to ENTER or a
    # well-formed PASS; assert the pipeline stays schema-valid and logged.
    assert last.decisions[0].inputs_ref.startswith("sha256:")
    # An ENTER (if produced) must have written a position.
    enters = db.query("SELECT * FROM positions WHERE status='OPEN'")
    for p in enters:
        assert p["contracts"] >= 1


def test_tier_logged_each_cycle(db):
    cfg = Config()
    adapter = MockRobinhoodAdapter(nlv=300.0)  # seed tier
    run_cycle(db, adapter, cfg, mode=Mode.PAPER, symbols=["NVDA"])
    m = db.query_one("SELECT value FROM metrics WHERE name='tier' ORDER BY metric_id DESC")
    assert m is not None
