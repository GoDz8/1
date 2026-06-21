"""Meta-loop — walk-forward re-validation (spec §11 Meta-loop).

Periodically re-tunes parameters on accumulated data using walk-forward analysis:
fit on a past window, validate on the next UNSEEN window, roll forward. Reports
OUT-OF-SAMPLE results only; never tunes in-sample. Point-in-time integrity is
mandatory — no lookahead leakage. Every adaptation writes a human-readable
learning report to the audit trail so the operator can see (and veto) it.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..storage.db import Database


@dataclass(frozen=True)
class WalkForwardReport:
    n_windows: int
    in_sample_mean: float
    out_of_sample_mean: float
    out_of_sample_std: float
    degradation: float       # in-sample minus out-of-sample (overfit signal)


def walk_forward(series: list[float], train: int, test: int) -> WalkForwardReport | None:
    """Roll a (train, test) window over an ordered series; report OOS stats.

    ``series`` must be in chronological order. Each window fits a trivial model
    (the in-sample mean) and scores it on the next unseen ``test`` points — a
    clean template that more complex parameter searches plug into without
    changing the point-in-time discipline.
    """
    arr = np.asarray(series, dtype=float)
    n = len(arr)
    if n < train + test:
        return None
    is_means, oos_vals = [], []
    start = 0
    while start + train + test <= n:
        in_sample = arr[start:start + train]
        out_sample = arr[start + train:start + train + test]
        is_means.append(float(in_sample.mean()))
        oos_vals.extend(out_sample.tolist())
        start += test  # roll forward by the test window (non-overlapping OOS)
    if not oos_vals:
        return None
    is_mean = float(np.mean(is_means))
    oos_mean = float(np.mean(oos_vals))
    return WalkForwardReport(
        n_windows=len(is_means), in_sample_mean=round(is_mean, 4),
        out_of_sample_mean=round(oos_mean, 4),
        out_of_sample_std=round(float(np.std(oos_vals, ddof=1)) if len(oos_vals) > 1 else 0.0, 4),
        degradation=round(is_mean - oos_mean, 4),
    )


def revalidate(db: Database, train: int = 20, test: int = 10) -> WalkForwardReport | None:
    """Walk-forward the realized expectancy-per-risk series and log a report."""
    rows = db.query(
        "SELECT d.capital_at_risk AS car, o.realized_pnl AS pnl "
        "FROM decisions d JOIN outcomes o ON o.decision_id = d.decision_id "
        "WHERE d.decision='ENTER' ORDER BY o.resolved_at ASC")
    series = [(r["pnl"] / r["car"]) for r in rows if (r["car"] or 0) > 0]
    rep = walk_forward(series, train, test)
    if rep is not None:
        db.record_metric("walkforward_oos_expectancy", rep.out_of_sample_mean,
                         context=f"windows={rep.n_windows} degradation={rep.degradation}")
        db.put_learning_state("walkforward_report", {
            "n_windows": rep.n_windows, "in_sample_mean": rep.in_sample_mean,
            "out_of_sample_mean": rep.out_of_sample_mean,
            "degradation": rep.degradation,
        })
    return rep
