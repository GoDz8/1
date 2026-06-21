"""Learning-refresh orchestration (spec §11).

Ties the layers together: write post-mortems for newly-resolved decisions, refit
the calibration map, and recompute the attribution suppression map. Sample-gated
inside each layer — calling this early is harmless (maps stay near identity/empty).
Every refresh leaves a human-readable trail in ``metrics`` / ``learning_state``.
"""

from __future__ import annotations

from ..config import Config
from ..logging_setup import get_logger
from ..storage.db import Database
from .attribution import SuppressionMap, update_attribution
from .calibration import CalibrationMap, update_calibration
from .metaloop import revalidate
from .postmortem import write_postmortem

_log = get_logger("learning")


def write_pending_postmortems(db: Database) -> int:
    """Write a post-mortem for every resolved decision that lacks one (Layer 5)."""
    rows = db.query(
        "SELECT o.decision_id AS did FROM outcomes o "
        "LEFT JOIN postmortems pm ON pm.decision_id = o.decision_id "
        "WHERE pm.postmortem_id IS NULL")
    count = 0
    for r in rows:
        if write_postmortem(db, r["did"]):
            count += 1
    return count


def refresh_learning(db: Database, cfg: Config,
                     run_metaloop: bool = True) -> tuple[CalibrationMap, SuppressionMap]:
    """Run the full learning refresh and return the updated maps."""
    n_pm = write_pending_postmortems(db)
    cmap = update_calibration(db, cfg.learning)
    smap = update_attribution(db, cfg.learning)
    if run_metaloop:
        revalidate(db)
    _log.info("learning refresh: %d post-mortems, calibration n=%d (w=%.2f), "
              "%d suppressed buckets", n_pm, cmap.n, cmap.shrink_weight,
              len(smap.suppressed))
    return cmap, smap
