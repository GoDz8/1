"""IV Rank and IV Percentile from our own rolling history of computed IV.

Spec §6: build the analytics; don't assume a paid feed. IV Rank/Percentile are
computed from a rolling window of *our own* daily ATM-IV observations.

Definitions (the standard ones):
- IV Rank      = (IV_now - IV_min) / (IV_max - IV_min)  over the window.
- IV Percentile = fraction of observations strictly below IV_now.

Both return a value in [0, 1], or None when the history is too short to be
meaningful (honesty rule §6 — better "unknown" than a fabricated rank).
"""

from __future__ import annotations

from collections.abc import Sequence

MIN_OBSERVATIONS = 20  # below this the rank is statistically meaningless


def iv_rank(current_iv: float, history: Sequence[float]) -> float | None:
    """IV Rank in [0, 1]. ``history`` is prior IV observations (window)."""
    obs = [float(x) for x in history if x is not None]
    if len(obs) < MIN_OBSERVATIONS:
        return None
    lo, hi = min(obs), max(obs)
    if hi - lo < 1e-12:
        return 0.0  # flat history — no rich/cheap signal
    rank = (current_iv - lo) / (hi - lo)
    return max(0.0, min(1.0, rank))


def iv_percentile(current_iv: float, history: Sequence[float]) -> float | None:
    """Fraction of historical observations strictly below ``current_iv``."""
    obs = [float(x) for x in history if x is not None]
    if len(obs) < MIN_OBSERVATIONS:
        return None
    below = sum(1 for x in obs if x < current_iv)
    return below / len(obs)
