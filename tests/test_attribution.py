"""Attribution & adaptive-gating tests (spec §11 Layer 3)."""

from datetime import datetime, timedelta, timezone

import pytest

from axiom.config import LearningConfig
from axiom.learning.attribution import (
    bucket_key, compute_bucket_stats, iv_rank_bucket, load_suppression,
    update_attribution, wilson_interval, _time_weight,
)

CFG = LearningConfig()


def test_iv_rank_buckets():
    assert iv_rank_bucket(None) == "unknown"
    assert iv_rank_bucket(0.1) == "ivr<30"
    assert iv_rank_bucket(0.4) == "ivr30-50"
    assert iv_rank_bucket(0.6) == "ivr50-70"
    assert iv_rank_bucket(0.9) == "ivr70+"


def test_wilson_interval_golden():
    lo, hi = wilson_interval(8, 10)
    assert lo == pytest.approx(0.490, abs=1e-2)
    assert hi == pytest.approx(0.943, abs=1e-2)


def test_wilson_interval_edge_cases():
    assert wilson_interval(0, 0) == (0.0, 1.0)
    lo, hi = wilson_interval(10, 10)
    assert hi == pytest.approx(1.0, abs=1e-9)


def test_time_weight_halflife():
    now = datetime(2026, 6, 21, tzinfo=timezone.utc)
    older = (now - timedelta(days=90)).isoformat()
    assert _time_weight(older, 90.0, now) == pytest.approx(0.5, abs=1e-6)
    assert _time_weight(now.isoformat(), 90.0, now) == pytest.approx(1.0, abs=1e-6)


def _seed_bucket(db, structure, regime, ivr, pnls, car=100.0):
    """Insert ENTER decisions + outcomes for one bucket with given P&Ls."""
    from axiom.storage.db import new_id, utcnow
    for pnl in pnls:
        did = new_id()
        db.record_decision({
            "decision_id": did, "mode": "PAPER", "symbol": "NVDA",
            "decision": "ENTER", "regime": regime, "inputs_ref": "sha256:x",
            "features": {"iv_rank": ivr},
            "structure": {"type": structure},
            "modeled_economics": {"pop": 0.7},
            "sizing": {"capital_at_risk": car},
        })
        db.record_outcome({"decision_id": did, "realized_pnl": pnl,
                           "win": pnl > 0, "resolved_at": utcnow()})


def test_negative_bucket_suppressed_with_enough_samples(db):
    # 25 losing trades in one bucket -> shrunk expectancy < 0 -> suppressed.
    _seed_bucket(db, "PUT_CREDIT_SPREAD", "CHOP", 0.6, [-50.0] * 25)
    stats = compute_bucket_stats(db, CFG)
    key = bucket_key("PUT_CREDIT_SPREAD", "CHOP", 0.6)
    assert stats[key].suppressed is True
    assert stats[key].shrunk_expectancy < 0


def test_thin_negative_bucket_not_suppressed(db):
    # Only 5 samples (< min_bucket_n) -> never acted on, even if negative.
    _seed_bucket(db, "IRON_CONDOR", "CHOP", 0.6, [-50.0] * 5)
    stats = compute_bucket_stats(db, CFG)
    key = bucket_key("IRON_CONDOR", "CHOP", 0.6)
    assert stats[key].suppressed is False


def test_positive_bucket_not_suppressed(db):
    _seed_bucket(db, "PUT_CREDIT_SPREAD", "RISK_ON", 0.8, [30.0] * 25)
    stats = compute_bucket_stats(db, CFG)
    key = bucket_key("PUT_CREDIT_SPREAD", "RISK_ON", 0.8)
    assert stats[key].suppressed is False
    assert stats[key].shrunk_expectancy > 0


def test_suppression_map_persists_and_loads(db):
    _seed_bucket(db, "PUT_CREDIT_SPREAD", "CHOP", 0.6, [-50.0] * 25)
    smap = update_attribution(db, CFG)
    assert smap.is_suppressed("PUT_CREDIT_SPREAD", "CHOP", 0.6)
    loaded = load_suppression(db)
    assert loaded.is_suppressed("PUT_CREDIT_SPREAD", "CHOP", 0.6)
    assert not loaded.is_suppressed("PUT_CREDIT_SPREAD", "RISK_ON", 0.8)
