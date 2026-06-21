"""Calibration engine tests (spec §11 Layer 2): scoring rules + isotonic map."""

import math

import pytest

from axiom.config import LearningConfig
from axiom.learning.calibration import (
    CalibrationMap, brier_score, fit_calibration, log_loss, reliability_diagram,
    _pava,
)

CFG = LearningConfig()


def test_brier_known_value():
    assert brier_score([(0.5, 1), (0.5, 0)]) == pytest.approx(0.25)
    assert brier_score([(1.0, 1), (0.0, 0)]) == pytest.approx(0.0)
    assert brier_score([]) is None


def test_log_loss_known_value():
    assert log_loss([(0.5, 1), (0.5, 0)]) == pytest.approx(-math.log(0.5), abs=1e-9)
    # Perfect, confident predictions -> ~0 loss (clipped).
    assert log_loss([(1.0, 1), (0.0, 0)]) == pytest.approx(0.0, abs=1e-6)


def test_pava_is_monotone_nondecreasing():
    import numpy as np
    x = np.array([0.1, 0.9, 0.2, 0.8])
    y = np.array([0.0, 0.0, 1.0, 1.0])
    xs, fitted = _pava(x, y)
    assert list(xs) == sorted(xs)
    assert all(fitted[i] <= fitted[i + 1] + 1e-12 for i in range(len(fitted) - 1))


def test_pava_pools_violators_to_mean():
    import numpy as np
    # Decreasing inputs must be pooled to their average (here all -> 0.5).
    x = np.array([0.1, 0.2, 0.3, 0.4])
    y = np.array([1.0, 1.0, 0.0, 0.0])
    _, fitted = _pava(x, y)
    assert fitted == pytest.approx([0.5, 0.5, 0.5, 0.5])


def test_identity_map_returns_input():
    cmap = CalibrationMap.identity()
    for p in (0.1, 0.5, 0.9):
        assert cmap.apply(p) == p


def test_shrink_toward_identity_below_gate():
    # n=10 with gate 50 -> shrink weight 0.2; apply blends mostly toward raw p.
    pairs = [(0.9, 0)] * 5 + [(0.9, 1)] * 5  # empirical freq 0.5 at p=0.9
    cmap = fit_calibration(pairs, CFG)
    assert cmap.shrink_weight == pytest.approx(10 / 50)
    out = cmap.apply(0.9)
    # blended 0.2*0.5 + 0.8*0.9 = 0.82
    assert out == pytest.approx(0.2 * 0.5 + 0.8 * 0.9, abs=1e-6)


def test_full_weight_above_gate_corrects_overconfidence():
    # 60 samples, all predicted 0.9 but only 50% win -> calibrated ~0.5.
    pairs = [(0.9, 1)] * 30 + [(0.9, 0)] * 30
    cmap = fit_calibration(pairs, CFG)
    assert cmap.shrink_weight == 1.0
    assert cmap.apply(0.9) == pytest.approx(0.5, abs=1e-6)


def test_reliability_diagram_bins():
    pairs = [(0.1, 0)] * 10 + [(0.9, 1)] * 10
    diag = reliability_diagram(pairs, n_bins=10)
    # Two populated bins, each perfectly calibrated.
    pred = {round(p, 1): (o, c) for p, o, c in diag}
    assert pred[0.1][0] == pytest.approx(0.0)
    assert pred[0.9][0] == pytest.approx(1.0)


def test_calibration_roundtrip_serialization():
    pairs = [(0.7, 1), (0.3, 0), (0.6, 1)]
    cmap = fit_calibration(pairs, CFG)
    again = CalibrationMap.from_dict(cmap.to_dict())
    assert again.apply(0.5) == pytest.approx(cmap.apply(0.5))
