"""IV Rank / Percentile tests."""

from axiom.quant.iv_rank import MIN_OBSERVATIONS, iv_percentile, iv_rank


def test_rank_midpoint():
    hist = [0.10 + 0.01 * i for i in range(21)]  # 0.10 .. 0.30
    # current at the midpoint 0.20 -> rank ~0.5
    assert abs(iv_rank(0.20, hist) - 0.5) < 1e-9


def test_rank_clamped_to_unit_interval():
    hist = [0.10 + 0.01 * i for i in range(21)]
    assert iv_rank(0.40, hist) == 1.0  # above max -> clamp to 1
    assert iv_rank(0.00, hist) == 0.0  # below min -> clamp to 0


def test_too_few_observations_returns_none():
    short = [0.2] * (MIN_OBSERVATIONS - 1)
    assert iv_rank(0.2, short) is None
    assert iv_percentile(0.2, short) is None


def test_flat_history_returns_zero_rank():
    hist = [0.2] * 30
    assert iv_rank(0.2, hist) == 0.0


def test_percentile():
    hist = list(range(100))  # 0..99
    assert iv_percentile(50, hist) == 0.5
    assert iv_percentile(0, hist) == 0.0
    assert iv_percentile(100, hist) == 1.0
