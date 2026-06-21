"""Layer 5 — structured post-mortems (spec §11 Layer 5: anti-'resulting').

On every close, classify the result into one of four cells — good/flawed process
× won/lost — extract a reusable lesson, and flag recurring error patterns. The
system reinforces PROCESS quality, not raw outcomes: a +EV trade that lost is
not punished; a −EV trade that won is not rewarded. This single rule prevents a
loop that teaches the system to pile into whatever recently got lucky.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..storage.db import Database


# The four cells of the process/outcome grid.
GOOD_WON = "good_process_won"
GOOD_LOST = "good_process_lost"
FLAWED_WON = "flawed_process_won"
FLAWED_LOST = "flawed_process_lost"


@dataclass(frozen=True)
class PostMortem:
    decision_id: str
    quadrant: str
    lesson: str
    error_flags: list[str]


def classify(good_process: bool, won: bool) -> str:
    if good_process and won:
        return GOOD_WON
    if good_process and not won:
        return GOOD_LOST
    if not good_process and won:
        return FLAWED_WON
    return FLAWED_LOST


def evaluate_process(decision_row, outcome_row) -> tuple[bool, list[str]]:
    """A trade had GOOD PROCESS if it cleared the gates it was supposed to:
    positive modeled EV, conviction at/above floor, all risk checks passed.
    Process quality is judged on the decision, NOT the realized result."""
    flags: list[str] = []
    good = True
    if (decision_row["ev_after_slippage"] or 0.0) <= 0:
        good = False
        flags.append("entered_non_positive_ev")  # should be impossible (gate)
    if (decision_row["conviction"] or 0) < 70:
        good = False
        flags.append("below_conviction_floor")
    # Outcome-side diagnostics (do NOT change the process verdict).
    if outcome_row["invalidation_triggered"]:
        flags.append("thesis_invalidated")
    rvm = outcome_row["realized_vs_modeled"]
    if rvm is not None and rvm < 0:
        flags.append("realized_below_model")
    return good, flags


def write_postmortem(db: Database, decision_id: str) -> PostMortem | None:
    """Build and persist a post-mortem for a resolved decision."""
    d = db.query_one("SELECT * FROM decisions WHERE decision_id=?", (decision_id,))
    o = db.query_one("SELECT * FROM outcomes WHERE decision_id=?", (decision_id,))
    if d is None or o is None:
        return None
    won = bool(o["win"])
    good, flags = evaluate_process(d, o)
    quadrant = classify(good, won)

    if quadrant == GOOD_LOST:
        lesson = ("Process was sound (EV+, conviction>=floor, checks passed); the "
                  "loss is variance, not a signal. Do not change behavior on this.")
    elif quadrant == FLAWED_WON:
        lesson = ("Won despite a flawed process — luck, not edge. Do not reinforce; "
                  "tighten the gate that should have blocked it.")
    elif quadrant == FLAWED_LOST:
        lesson = ("Flawed process and lost. Fix the gate(s): " + ", ".join(flags))
    else:
        lesson = "Good process, won as expected. Keep the discipline."

    pm = PostMortem(decision_id, quadrant, lesson, flags)
    db.record_postmortem({
        "decision_id": decision_id, "quadrant": quadrant, "lesson": lesson,
        "error_flags": flags,
    })
    return pm
