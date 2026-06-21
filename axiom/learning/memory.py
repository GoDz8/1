"""Layer 4 — retrieval-augmented memory (spec §11 Layer 4).

Before each decision, retrieve the k most-similar resolved past cases (same name /
cluster / regime / structure / IV-rank neighborhood, prioritizing recency) and
inject them into the reasoning context: prior thesis, what it predicted, what
happened, the P&L, and the post-mortem lesson. The AI conditions on its own
documented history — in-context learning with no retraining.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import Config
from ..storage.db import Database
from .attribution import iv_rank_bucket


@dataclass(frozen=True)
class PastCase:
    symbol: str
    regime: str
    structure_type: str
    iv_rank: float | None
    thesis: str
    pop: float | None
    realized_pnl: float
    win: bool
    lesson: str | None
    score: float

    def as_context(self) -> str:
        verdict = "WON" if self.win else "LOST"
        lesson = f" Lesson: {self.lesson}" if self.lesson else ""
        return (f"[{self.symbol} {self.structure_type} in {self.regime}, "
                f"IVR {iv_rank_bucket(self.iv_rank)}] predicted POP "
                f"{self.pop if self.pop is not None else '?'}, {verdict} "
                f"${self.realized_pnl:+.0f}. Thesis: {self.thesis[:120]}{lesson}")


def retrieve_similar(db: Database, cfg: Config, symbol: str, regime: str,
                     structure_type: str, iv_rank: float | None,
                     k: int | None = None) -> list[PastCase]:
    """Return the top-k most similar resolved cases by feature similarity."""
    k = k or cfg.learning.retrieval_k
    cluster = cfg.cluster_for(symbol)
    rows = db.query(
        "SELECT d.symbol AS sym, d.regime AS rg, d.structure_type AS st, "
        "d.iv_rank AS ivr, d.thesis AS th, d.pop AS pop, d.timestamp AS ts, "
        "o.realized_pnl AS pnl, o.win AS win, pm.lesson AS lesson "
        "FROM decisions d JOIN outcomes o ON o.decision_id = d.decision_id "
        "LEFT JOIN postmortems pm ON pm.decision_id = d.decision_id "
        "WHERE d.decision='ENTER' ORDER BY d.timestamp DESC LIMIT 500")

    scored: list[PastCase] = []
    for i, r in enumerate(rows):
        score = 0.0
        if r["sym"] == symbol:
            score += 3.0
        elif cluster and cfg.cluster_for(r["sym"]) == cluster:
            score += 2.0
        if r["rg"] == regime:
            score += 2.0
        if r["st"] == structure_type:
            score += 2.0
        if r["ivr"] is not None and iv_rank is not None:
            score += max(0.0, 1.0 - abs(r["ivr"] - iv_rank))
        score += max(0.0, 0.5 - i * 0.001)  # mild recency tiebreaker
        if score <= 0:
            continue
        scored.append(PastCase(
            symbol=r["sym"], regime=r["rg"], structure_type=r["st"],
            iv_rank=r["ivr"], thesis=r["th"] or "", pop=r["pop"],
            realized_pnl=r["pnl"] or 0.0, win=bool(r["win"]),
            lesson=r["lesson"], score=score,
        ))
    scored.sort(key=lambda c: c.score, reverse=True)
    return scored[:k]
