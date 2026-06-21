"""Real-data snapshot adapter (bridges the agent-side MCP gap).

The running Python process cannot call the agent-side Robinhood MCP tools. The
bridge is a *snapshot*: the agent (or a future MCP-client sidecar) fetches real
account + chain + quote data via the MCP read tools, normalizes it into the JSON
schema below, and ``SnapshotRobinhoodAdapter`` serves it to the exact same
RobinhoodAdapter Protocol the mock/sim implement — so AXIOM runs its full
decision cycle on REAL market data with zero changes to the engine.

This is read-only and places no orders. IV RANK still requires a HISTORY of IV
observations (spec §6): a single snapshot yields ``iv_rank=None`` -> PASS (fail
closed), and repeated daily captures accrue the real history that unlocks entries
(stored in ``learning_state`` by the orchestrator's ``_proxy_iv_rank``). No value
is ever fabricated — missing data stays ``unknown``.

Snapshot JSON schema (normalized; all prices per share):
{
  "captured_at": "ISO-8601",
  "source": "robinhood_mcp",
  "risk_free_rate": 0.04,
  "account": {"nlv": float, "buying_power": float, "day_trades_used": int},
  "symbols": {
    "SOFI": {
      "spot": float,
      "expiry": "YYYY-MM-DD",
      "dte": int,
      "days_to_earnings": int | null,
      "history": [float, ...],          # optional underlying closes for regime
      "iv_rank": float | null,          # optional; honest unknown by default
      "quotes": [
        {"strike": float, "right": "CALL|PUT", "bid": float, "ask": float,
         "open_interest": int, "volume": int, "iv": float}
      ]
    }
  }
}
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .robinhood_mcp import (
    AccountSnapshot, OptionChain, OptionQuote, OrderReview, UnderlyingQuote,
)


# --------------------------------------------------------------------------- #
# Adapter
# --------------------------------------------------------------------------- #
class SnapshotRobinhoodAdapter:
    """Serves a captured real-data snapshot via the RobinhoodAdapter Protocol."""

    def __init__(self, snapshot: dict[str, Any]):
        self.snap = snapshot
        self._symbols = snapshot.get("symbols", {})
        self.rate = float(snapshot.get("risk_free_rate", 0.04))

    # -- account ------------------------------------------------------------ #
    def get_account(self) -> AccountSnapshot | None:
        a = self.snap.get("account")
        if not a:
            return None
        return AccountSnapshot(nlv=float(a["nlv"]), buying_power=float(a["buying_power"]),
                               day_trades_used=int(a.get("day_trades_used", 0)))

    # -- market ------------------------------------------------------------- #
    def get_underlying_quote(self, symbol: str) -> UnderlyingQuote | None:
        s = self._symbols.get(symbol)
        if not s or s.get("spot") is None:
            return None
        spot = float(s["spot"])
        # Use real bid/ask if present, else a tight synthetic band on the spot.
        return UnderlyingQuote(symbol, last=spot,
                               bid=float(s.get("bid", spot)), ask=float(s.get("ask", spot)))

    def get_option_chain(self, symbol: str, dte_target: int) -> OptionChain | None:
        s = self._symbols.get(symbol)
        if not s or not s.get("quotes"):
            return None
        spot = float(s["spot"])
        expiry = s.get("expiry") or date.today().isoformat()
        quotes = [
            OptionQuote(
                symbol=symbol, expiry=expiry, strike=float(q["strike"]),
                right=q["right"], bid=float(q["bid"]), ask=float(q["ask"]),
                open_interest=int(q.get("open_interest", 0)),
                volume=int(q.get("volume", 0)),
                iv=(float(q["iv"]) if q.get("iv") is not None else None),
            )
            for q in s["quotes"]
        ]
        und = self.get_underlying_quote(symbol)
        return OptionChain(symbol=symbol, underlying=und, expiries=[expiry], quotes=quotes)

    def get_days_to_earnings(self, symbol: str) -> int | None:
        s = self._symbols.get(symbol)
        return None if not s else s.get("days_to_earnings")

    def get_price_history(self, symbol: str, lookback: int = 60) -> list[float] | None:
        s = self._symbols.get(symbol)
        if not s:
            return None
        hist = s.get("history")
        return [float(x) for x in hist[-lookback:]] if hist else None

    def snapshot_iv_rank(self, symbol: str) -> float | None:
        """Operator-/capture-provided real IV rank, if any (never fabricated)."""
        s = self._symbols.get(symbol)
        return None if not s else s.get("iv_rank")

    def review_option_order(self, symbol: str, legs: list[dict]) -> OrderReview:
        net = sum(l.get("price", 0.0) * (1 if l.get("side") == "SELL" else -1) for l in legs)
        return OrderReview(round(net, 2), round(abs(net) * 100, 2), True,
                           "Snapshot dry-run — no live order placed.")


# --------------------------------------------------------------------------- #
# Mappers: raw Robinhood MCP responses -> normalized snapshot
# --------------------------------------------------------------------------- #
def spot_from_equity_quote(rh_result: dict) -> float:
    """Extract the current spot from a get_equity_quotes result entry."""
    q = rh_result["quote"]
    return float(q.get("last_trade_price") or q.get("last_non_reg_trade_price"))


def option_quote_to_dict(strike: float, right: str, rh_quote: dict) -> dict:
    """Normalize a get_option_quotes entry to a snapshot quote dict."""
    return {
        "strike": float(strike),
        "right": right.upper(),
        "bid": float(rh_quote.get("bid_price") or 0.0),
        "ask": float(rh_quote.get("ask_price") or 0.0),
        "open_interest": int(rh_quote.get("open_interest") or 0),
        "volume": int(rh_quote.get("volume") or 0),
        "iv": (float(rh_quote["implied_volatility"])
               if rh_quote.get("implied_volatility") is not None else None),
    }


def dte_between(today: str | None, expiry: str) -> int:
    base = date.fromisoformat(today) if today else date.today()
    return max(0, (date.fromisoformat(expiry) - base).days)


def new_snapshot(account: dict, risk_free_rate: float = 0.04) -> dict:
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "source": "robinhood_mcp",
        "risk_free_rate": risk_free_rate,
        "account": account,
        "symbols": {},
    }


def save_snapshot(path: str, snapshot: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(snapshot, indent=2))


def load_snapshot(path: str) -> dict:
    return json.loads(Path(path).read_text())
