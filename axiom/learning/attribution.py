"""Layer 3 — strategy attribution & adaptive gating (spec §11 Layer 3).

Deterministic (no hallucination risk). Computes rolling realized expectancy per
feature bucket with a confidence interval and Bayesian shrinkage toward a
neutral prior, time-decays the weighting for regime non-stationarity, and emits
a suppression map fed into §3 gating: buckets with statistically significant
negative realized expectancy get blocked; everything else gets normal access.
Never acts on a bucket below ``min_bucket_n``.

This layer adjusts edge estimates and access ONLY — it can never touch the §5.6
risk caps or kill switches.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..config import LearningConfig
from ..storage.db import Database


def iv_rank_bucket(iv_rank: float | None) -> str:
    if iv_rank is None:
        return "unknown"
    if iv_rank < 0.30:
        return "ivr<30"
    if iv_rank < 0.50:
        return "ivr30-50"
    if iv_rank < 0.70:
        return "ivr50-70"
    return "ivr70+"


def bucket_key(structure_type: str | None, regime: str | None, iv_rank: float | None) -> str:
    return f"{structure_type or '?'}|{regime or '?'}|{iv_rank_bucket(iv_rank)}"


def wilson_interval(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a binomial hit-rate (spec §11 Layer 3)."""
    if n == 0:
        return (0.0, 1.0)
    phat = wins / n
    denom = 1 + z * z / n
    center = (phat + z * z / (2 * n)) / denom
    margin = (z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n))) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


@dataclass(frozen=True)
class BucketStat:
    key: str
    n: int
    hit_rate: float
    hit_lo: float
    hit_hi: float
    raw_expectancy: float       # time-weighted mean realized P&L per $ risk
    shrunk_expectancy: float    # Bayesian-shrunk toward 0 (neutral prior)
    suppressed: bool


def _time_weight(resolved_at: str, halflife_days: float, now: datetime) -> float:
    try:
        ts = datetime.fromisoformat(resolved_at)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return 1.0
    age_days = max(0.0, (now - ts).total_seconds() / 86400.0)
    return 0.5 ** (age_days / max(halflife_days, 1e-9))


def compute_bucket_stats(db: Database, cfg: LearningConfig) -> dict[str, BucketStat]:
    """Aggregate realized outcomes into per-bucket statistics."""
    rows = db.query(
        "SELECT d.structure_type AS st, d.regime AS rg, d.iv_rank AS ivr, "
        "d.capital_at_risk AS car, o.realized_pnl AS pnl, o.win AS win, "
        "o.resolved_at AS ra FROM decisions d "
        "JOIN outcomes o ON o.decision_id = d.decision_id "
        "WHERE d.decision='ENTER'")
    now = datetime.now(timezone.utc)
    agg: dict[str, dict] = {}
    for r in rows:
        key = bucket_key(r["st"], r["rg"], r["ivr"])
        car = r["car"] or 0.0
        if car <= 0:
            continue
        epr = (r["pnl"] or 0.0) / car
        w = _time_weight(r["ra"], cfg.time_decay_halflife_days, now)
        a = agg.setdefault(key, {"n": 0, "wins": 0, "wsum": 0.0, "wepr": 0.0})
        a["n"] += 1
        a["wins"] += int(r["win"])
        a["wsum"] += w
        a["wepr"] += w * epr

    out: dict[str, BucketStat] = {}
    k = cfg.shrinkage_k
    for key, a in agg.items():
        n = a["n"]
        raw = a["wepr"] / a["wsum"] if a["wsum"] > 0 else 0.0
        # Bayesian shrinkage toward a neutral (0) prior expectancy.
        shrunk = (0.0 * k + raw * n) / (k + n)
        lo, hi = wilson_interval(a["wins"], n)
        suppressed = (n >= cfg.min_bucket_n
                      and shrunk < cfg.suppress_expectancy_threshold)
        out[key] = BucketStat(key, n, a["wins"] / n, lo, hi, raw, shrunk, suppressed)
    return out


@dataclass(frozen=True)
class SuppressionMap:
    suppressed: frozenset[str] = field(default_factory=frozenset)

    def is_suppressed(self, structure_type: str, regime: str, iv_rank: float | None) -> bool:
        return bucket_key(structure_type, regime, iv_rank) in self.suppressed

    def to_dict(self) -> dict:
        return {"suppressed": sorted(self.suppressed)}

    @staticmethod
    def from_dict(d: dict) -> "SuppressionMap":
        return SuppressionMap(frozenset(d.get("suppressed", [])))


def update_attribution(db: Database, cfg: LearningConfig) -> SuppressionMap:
    """Recompute bucket stats, persist a suppression map, log a learning report."""
    stats = compute_bucket_stats(db, cfg)
    suppressed = frozenset(k for k, s in stats.items() if s.suppressed)
    smap = SuppressionMap(suppressed)
    db.put_learning_state("suppression_map", smap.to_dict())
    for s in stats.values():
        db.record_metric("bucket_expectancy", s.shrunk_expectancy,
                         context=f"{s.key} n={s.n} suppressed={s.suppressed}")
    return smap


def load_suppression(db: Database) -> SuppressionMap:
    d = db.get_learning_state("suppression_map")
    return SuppressionMap.from_dict(d) if d else SuppressionMap()
