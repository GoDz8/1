"""Layer 2 — calibration engine (spec §11 Layer 2).

Conviction and POP are probabilistic claims; we grade them with proper scoring
rules (Brier, log-loss), build a reliability diagram, and fit an isotonic
calibration map that corrects systematic over/under-confidence. The EV gate then
runs on EMPIRICALLY-CORRECTED probabilities, not the raw stated ones.

Sample gate (spec §11): measure from day one, but shrink the map toward identity
below ``min_resolved_for_calibration`` resolved forecasts so a tiny sample can't
swing the system.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..config import LearningConfig
from ..storage.db import Database

EPS = 1e-9


def brier_score(pairs: list[tuple[float, int]]) -> float | None:
    """Mean squared error of probabilistic forecasts. Lower is better."""
    if not pairs:
        return None
    p = np.array([x[0] for x in pairs], dtype=float)
    o = np.array([x[1] for x in pairs], dtype=float)
    return float(np.mean((p - o) ** 2))


def log_loss(pairs: list[tuple[float, int]]) -> float | None:
    if not pairs:
        return None
    p = np.clip(np.array([x[0] for x in pairs], dtype=float), EPS, 1 - EPS)
    o = np.array([x[1] for x in pairs], dtype=float)
    return float(-np.mean(o * np.log(p) + (1 - o) * np.log(1 - p)))


def reliability_diagram(pairs: list[tuple[float, int]], n_bins: int = 10):
    """Return per-bin (predicted_mean, observed_freq, count)."""
    if not pairs:
        return []
    p = np.array([x[0] for x in pairs], dtype=float)
    o = np.array([x[1] for x in pairs], dtype=float)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    out = []
    for i in range(n_bins):
        lo, hi = edges[i], edges[i + 1]
        mask = (p >= lo) & (p < hi if i < n_bins - 1 else p <= hi)
        if mask.sum() == 0:
            continue
        out.append((float(p[mask].mean()), float(o[mask].mean()), int(mask.sum())))
    return out


def _pava(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Pool-Adjacent-Violators isotonic regression (non-decreasing fit)."""
    order = np.argsort(x, kind="mergesort")
    xs, ys = x[order].astype(float), y[order].astype(float)
    n = len(ys)
    # Blocks: value, weight, count
    vals = ys.copy()
    wts = np.ones(n)
    idx = list(range(n))  # block boundaries as a stack
    level_val: list[float] = []
    level_w: list[float] = []
    for i in range(n):
        level_val.append(vals[i])
        level_w.append(wts[i])
        # Pool while monotonicity is violated.
        while len(level_val) > 1 and level_val[-2] > level_val[-1]:
            v2, w2 = level_val.pop(), level_w.pop()
            v1, w1 = level_val.pop(), level_w.pop()
            merged_w = w1 + w2
            level_val.append((v1 * w1 + v2 * w2) / merged_w)
            level_w.append(merged_w)
    # Expand pooled values back to each point.
    fitted = np.empty(n)
    pos = 0
    for v, w in zip(level_val, level_w):
        cnt = int(round(w))
        fitted[pos:pos + cnt] = v
        pos += cnt
    return xs, fitted


@dataclass(frozen=True)
class CalibrationMap:
    x: list[float]            # sorted predicted probabilities
    y: list[float]            # fitted (calibrated) probabilities
    n: int                    # resolved sample size
    shrink_weight: float      # blend weight toward the fitted map (0..1)

    def apply(self, p: float) -> float:
        """Map a raw probability to its calibrated value, shrunk toward identity."""
        if not self.x:
            return p
        cal = float(np.interp(p, self.x, self.y, left=self.y[0], right=self.y[-1]))
        return self.shrink_weight * cal + (1 - self.shrink_weight) * p

    def to_dict(self) -> dict:
        return {"x": self.x, "y": self.y, "n": self.n, "shrink_weight": self.shrink_weight}

    @staticmethod
    def identity() -> "CalibrationMap":
        return CalibrationMap(x=[], y=[], n=0, shrink_weight=0.0)

    @staticmethod
    def from_dict(d: dict) -> "CalibrationMap":
        return CalibrationMap(d["x"], d["y"], d["n"], d["shrink_weight"])


def fit_calibration(pairs: list[tuple[float, int]], cfg: LearningConfig) -> CalibrationMap:
    """Fit an isotonic calibration map, shrunk toward identity below the sample gate."""
    n = len(pairs)
    if n == 0:
        return CalibrationMap.identity()
    x = np.array([p for p, _ in pairs], dtype=float)
    o = np.array([oo for _, oo in pairs], dtype=float)
    xs, fitted = _pava(x, o)
    # Collapse duplicate x to keep np.interp strictly increasing. Tied x must map
    # to ONE value — the mean of their fitted values (proper isotonic pooling),
    # NOT the first occurrence, or a cluster of identical predictions collapses to
    # the wrong calibrated probability.
    ux = np.unique(xs)
    uy = np.array([fitted[xs == u].mean() for u in ux])
    weight = min(1.0, n / max(cfg.min_resolved_for_calibration, 1))
    return CalibrationMap(x=ux.tolist(), y=uy.tolist(), n=n, shrink_weight=weight)


def collect_pairs(db: Database) -> list[tuple[float, int]]:
    """Gather (predicted_prob, outcome) pairs from resolved forecasts AND from
    ENTER decisions joined to their realized outcomes (spec §11 Layer 1/2)."""
    pairs: list[tuple[float, int]] = []
    for r in db.query("SELECT predicted_prob, resolved_outcome FROM forecasts "
                      "WHERE resolved=1 AND resolved_outcome IS NOT NULL"):
        pairs.append((float(r["predicted_prob"]), int(r["resolved_outcome"])))
    for r in db.query(
        "SELECT d.pop AS pop, o.win AS win FROM decisions d "
        "JOIN outcomes o ON o.decision_id = d.decision_id "
        "WHERE d.decision='ENTER' AND d.pop IS NOT NULL"):
        pairs.append((float(r["pop"]), int(r["win"])))
    return pairs


def update_calibration(db: Database, cfg: LearningConfig) -> CalibrationMap:
    """Refit and persist the calibration map; record scoring metrics."""
    pairs = collect_pairs(db)
    cmap = fit_calibration(pairs, cfg)
    db.put_learning_state("calibration_map", cmap.to_dict())
    bs, ll = brier_score(pairs), log_loss(pairs)
    if bs is not None:
        db.record_metric("brier", bs, context=f"n={len(pairs)}")
    if ll is not None:
        db.record_metric("log_loss", ll, context=f"n={len(pairs)}")
    return cmap


def load_calibration(db: Database) -> CalibrationMap:
    d = db.get_learning_state("calibration_map")
    return CalibrationMap.from_dict(d) if d else CalibrationMap.identity()
