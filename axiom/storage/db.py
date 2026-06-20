"""SQLite database access for the audit trail (spec §0 / §7).

Thin, dependency-light wrapper: schema migration on connect, plus typed-ish
insert/query helpers. The full §8 decision JSON is always stored in
``payload_json`` for fidelity, with feature columns duplicated for fast
bucketed queries (the learning loop joins by ``decision_id``).
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..logging_setup import get_logger

_log = get_logger("storage")
_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return str(uuid.uuid4())


class Database:
    """Owns a SQLite connection and applies the schema on construction."""

    def __init__(self, path: str = "axiom.db"):
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON;")
        self._migrate()

    def _migrate(self) -> None:
        self.conn.executescript(_SCHEMA_PATH.read_text())
        self.conn.commit()
        _log.debug("schema applied to %s", self.path)

    # -- generic helpers ---------------------------------------------------- #
    def insert(self, table: str, row: dict[str, Any]) -> None:
        cols = ", ".join(row)
        placeholders = ", ".join(["?"] * len(row))
        self.conn.execute(
            f"INSERT INTO {table} ({cols}) VALUES ({placeholders})",
            tuple(row.values()),
        )
        self.conn.commit()

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchall()

    def query_one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, params).fetchone()

    # -- domain inserts ----------------------------------------------------- #
    def record_decision(self, decision: dict[str, Any]) -> None:
        """Persist a §8 decision dict. Flattens feature/economics columns."""
        feats = decision.get("features", {}) or {}
        econ = decision.get("modeled_economics", {}) or {}
        sizing = decision.get("sizing", {}) or {}
        structure = decision.get("structure", {}) or {}
        row = {
            "decision_id": decision["decision_id"],
            "timestamp": decision.get("timestamp", utcnow()),
            "mode": decision["mode"],
            "symbol": decision["symbol"],
            "decision": decision["decision"],
            "regime": decision["regime"],
            "iv_rank": feats.get("iv_rank"),
            "iv_percentile": feats.get("iv_percentile"),
            "sector_cluster": feats.get("sector_cluster"),
            "catalyst_type": feats.get("catalyst_type"),
            "dte_bucket": feats.get("dte_bucket"),
            "days_to_earnings": feats.get("days_to_earnings"),
            "conviction": decision.get("conviction"),
            "structure_type": structure.get("type"),
            "dte": structure.get("dte"),
            "credit_or_debit": econ.get("credit_or_debit"),
            "max_profit": econ.get("max_profit"),
            "max_loss": econ.get("max_loss"),
            "pop": econ.get("pop"),
            "ev_after_slippage": econ.get("ev_after_slippage"),
            "ev_per_dollar_risk": econ.get("ev_per_dollar_risk"),
            "kelly_fraction": sizing.get("kelly_fraction"),
            "applied_fraction": sizing.get("applied_fraction"),
            "contracts": sizing.get("contracts"),
            "capital_at_risk": sizing.get("capital_at_risk"),
            "pct_of_nlv": sizing.get("pct_of_nlv"),
            "thesis": decision.get("thesis"),
            "invalidation": decision.get("invalidation"),
            "inputs_ref": decision["inputs_ref"],
            "payload_json": json.dumps(decision, default=str),
        }
        self.insert("decisions", row)
        _log.info("recorded decision %s %s %s",
                  decision["decision_id"][:8], decision["symbol"], decision["decision"])

    def record_candidate(self, candidate: dict[str, Any]) -> None:
        row = {
            "candidate_id": candidate.get("candidate_id", new_id()),
            "decision_id": candidate.get("decision_id"),
            "timestamp": candidate.get("timestamp", utcnow()),
            "symbol": candidate["symbol"],
            "structure_type": candidate.get("structure_type"),
            "iv_rank": candidate.get("iv_rank"),
            "pop": candidate.get("pop"),
            "ev_after_slippage": candidate.get("ev_after_slippage"),
            "ev_per_dollar_risk": candidate.get("ev_per_dollar_risk"),
            "rejected_reason": candidate.get("rejected_reason"),
            "payload_json": json.dumps(candidate, default=str),
        }
        self.insert("candidates", row)

    def record_order(self, order: dict[str, Any]) -> None:
        order.setdefault("order_id", new_id())
        row = {
            "order_id": order["order_id"],
            "decision_id": order["decision_id"],
            "timestamp": order.get("timestamp", utcnow()),
            "mode": order["mode"],
            "symbol": order["symbol"],
            "side": order.get("side"),
            "status": order["status"],
            "limit_price": order.get("limit_price"),
            "payload_json": json.dumps(order, default=str),
        }
        self.insert("orders", row)

    def record_fill(self, fill: dict[str, Any]) -> None:
        fill.setdefault("fill_id", new_id())
        row = {
            "fill_id": fill["fill_id"],
            "order_id": fill["order_id"],
            "timestamp": fill.get("timestamp", utcnow()),
            "fill_price": fill["fill_price"],
            "contracts": fill["contracts"],
            "fees": fill.get("fees", 0.0),
            "simulated": 1 if fill.get("simulated", True) else 0,
            "payload_json": json.dumps(fill, default=str),
        }
        self.insert("fills", row)

    def record_forecast(self, forecast: dict[str, Any]) -> None:
        forecast.setdefault("forecast_id", new_id())
        row = {
            "forecast_id": forecast["forecast_id"],
            "decision_id": forecast.get("decision_id"),
            "timestamp": forecast.get("timestamp", utcnow()),
            "symbol": forecast["symbol"],
            "statement": forecast["statement"],
            "predicted_prob": forecast["predicted_prob"],
            "resolution_date": forecast["resolution_date"],
            "resolved": 1 if forecast.get("resolved") else 0,
            "resolved_outcome": forecast.get("resolved_outcome"),
            "payload_json": json.dumps(forecast, default=str),
        }
        self.insert("forecasts", row)

    def record_outcome(self, outcome: dict[str, Any]) -> None:
        outcome.setdefault("outcome_id", new_id())
        row = {
            "outcome_id": outcome["outcome_id"],
            "decision_id": outcome["decision_id"],
            "resolved_at": outcome.get("resolved_at", utcnow()),
            "realized_pnl": outcome["realized_pnl"],
            "win": 1 if outcome["win"] else 0,
            "invalidation_triggered": 1 if outcome.get("invalidation_triggered") else 0,
            "realized_vs_modeled": outcome.get("realized_vs_modeled"),
            "payload_json": json.dumps(outcome, default=str),
        }
        self.insert("outcomes", row)

    def record_postmortem(self, pm: dict[str, Any]) -> None:
        pm.setdefault("postmortem_id", new_id())
        row = {
            "postmortem_id": pm["postmortem_id"],
            "decision_id": pm["decision_id"],
            "timestamp": pm.get("timestamp", utcnow()),
            "quadrant": pm["quadrant"],
            "lesson": pm["lesson"],
            "error_flags": json.dumps(pm.get("error_flags", [])),
            "payload_json": json.dumps(pm, default=str),
        }
        self.insert("postmortems", row)

    def record_metric(self, name: str, value: float, context: str | None = None) -> None:
        self.insert("metrics", {
            "timestamp": utcnow(), "name": name, "value": value, "context": context,
        })

    # -- learning state (persisted across restarts) ------------------------- #
    def put_learning_state(self, key: str, value: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT INTO learning_state (key, updated_at, value_json) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET updated_at=excluded.updated_at, "
            "value_json=excluded.value_json",
            (key, utcnow(), json.dumps(value, default=str)),
        )
        self.conn.commit()

    def get_learning_state(self, key: str) -> dict[str, Any] | None:
        row = self.query_one("SELECT value_json FROM learning_state WHERE key=?", (key,))
        return json.loads(row["value_json"]) if row else None

    def close(self) -> None:
        self.conn.close()
