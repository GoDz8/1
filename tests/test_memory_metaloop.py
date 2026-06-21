"""Retrieval memory (Layer 4) + walk-forward meta-loop tests."""

import pytest

from axiom.config import Config
from axiom.learning.memory import retrieve_similar
from axiom.learning.metaloop import walk_forward
from axiom.storage.db import new_id, utcnow

CFG = Config()


def _enter_with_outcome(db, symbol, regime, structure, ivr, pnl, win):
    did = new_id()
    db.record_decision({
        "decision_id": did, "mode": "PAPER", "symbol": symbol,
        "decision": "ENTER", "regime": regime, "inputs_ref": "sha256:x",
        "thesis": f"{structure} on {symbol}",
        "features": {"iv_rank": ivr}, "structure": {"type": structure},
        "modeled_economics": {"pop": 0.7},
    })
    db.record_outcome({"decision_id": did, "realized_pnl": pnl, "win": win,
                       "resolved_at": utcnow()})
    return did


def test_retrieval_prioritizes_same_symbol_regime_structure(db):
    _enter_with_outcome(db, "NVDA", "CHOP", "PUT_CREDIT_SPREAD", 0.6, 50, True)
    _enter_with_outcome(db, "XOM", "RISK_OFF", "IRON_CONDOR", 0.2, -30, False)
    cases = retrieve_similar(db, CFG, "NVDA", "CHOP", "PUT_CREDIT_SPREAD", 0.6)
    assert cases
    assert cases[0].symbol == "NVDA"
    assert cases[0].score >= cases[-1].score


def test_retrieval_empty_when_no_history(db):
    assert retrieve_similar(db, CFG, "NVDA", "CHOP", "PUT_CREDIT_SPREAD", 0.6) == []


def test_retrieval_respects_k(db):
    for i in range(10):
        _enter_with_outcome(db, "NVDA", "CHOP", "PUT_CREDIT_SPREAD", 0.6, 10, True)
    cases = retrieve_similar(db, CFG, "NVDA", "CHOP", "PUT_CREDIT_SPREAD", 0.6, k=3)
    assert len(cases) == 3


def test_walk_forward_oos_no_lookahead():
    # Stationary mean series: in-sample ~ out-of-sample, low degradation.
    series = [1.0, -1.0] * 50
    rep = walk_forward(series, train=20, test=10)
    assert rep is not None
    assert rep.n_windows >= 1
    assert abs(rep.degradation) < 0.5


def test_walk_forward_detects_degradation():
    # Good in-sample then bad out-of-sample within each rolled window.
    series = [1.0] * 20 + [-1.0] * 10 + [1.0] * 20 + [-1.0] * 10
    rep = walk_forward(series, train=20, test=10)
    assert rep is not None
    assert rep.degradation > 0  # in-sample optimistic vs OOS


def test_walk_forward_too_short_returns_none():
    assert walk_forward([1.0, 2.0, 3.0], train=20, test=10) is None
