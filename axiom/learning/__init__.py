"""Continuous-learning loop (spec §11). Phase 1 ships Layer 1 (capture) only.

Layers 2-5 (calibration, attribution/adaptive gating, retrieval memory,
post-mortems) and the walk-forward meta-loop activate after Phase 2 has
accumulated a real sample — acting on tiny samples makes the system worse.
"""
