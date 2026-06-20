"""Robinhood adapter (spec §6 — primary account/quote/chain/order source).

IMPORTANT ARCHITECTURE NOTE
---------------------------
The Robinhood MCP connector (https://agent.robinhood.com/mcp/trading) is an
*agent-side* tool surface, not a Python library this process can import. Live
auth to the funded account is not available in Phase 1. So this module defines:

  * ``RobinhoodAdapter`` — a Protocol describing the read + dry-run surface the
    rest of AXIOM depends on (account, chains, quotes, earnings, review_order).
  * ``MockRobinhoodAdapter`` — deterministic fixtures used by paper mode and the
    test suite.

A real MCP-client-backed implementation is the Phase-3 integration seam
(``LiveRobinhoodAdapter``, not built here). NOTHING in this module places a live
order — the only order method is ``review_option_order`` (dry-run), per spec §8.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class AccountSnapshot:
    nlv: float                 # net liquidation value
    buying_power: float
    day_trades_used: int       # in the rolling 5 business days


@dataclass(frozen=True)
class UnderlyingQuote:
    symbol: str
    last: float
    bid: float
    ask: float


@dataclass(frozen=True)
class OptionQuote:
    symbol: str
    expiry: str               # YYYY-MM-DD
    strike: float
    right: str                # CALL | PUT
    bid: float
    ask: float
    open_interest: int
    volume: int
    iv: float | None = None   # provider IV if present; else AXIOM solves it


@dataclass(frozen=True)
class OptionChain:
    symbol: str
    underlying: UnderlyingQuote
    expiries: list[str]
    quotes: list[OptionQuote] = field(default_factory=list)


@dataclass(frozen=True)
class OrderReview:
    """Result of a dry-run review_option_order (spec §8 pre-flight)."""

    estimated_net_price: float   # per-contract net credit(+)/debit(-)
    buying_power_effect: float
    acceptable: bool
    note: str = ""


@runtime_checkable
class RobinhoodAdapter(Protocol):
    def get_account(self) -> AccountSnapshot | None: ...
    def get_underlying_quote(self, symbol: str) -> UnderlyingQuote | None: ...
    def get_option_chain(self, symbol: str, dte_target: int) -> OptionChain | None: ...
    def get_days_to_earnings(self, symbol: str) -> int | None: ...
    def review_option_order(self, symbol: str, legs: list[dict]) -> OrderReview | None: ...


def _seed(symbol: str) -> int:
    return int(hashlib.sha256(symbol.encode()).hexdigest(), 16) % 10_000


class MockRobinhoodAdapter:
    """Deterministic fixtures for paper mode + tests.

    Spot, IV, and earnings distance are derived from the symbol hash so runs are
    reproducible. The chain is a synthetic but internally consistent grid priced
    with the AXIOM Black-Scholes model so EV/Greeks math has real numbers to act
    on.
    """

    def __init__(self, nlv: float = 300.0, day_trades_used: int = 0,
                 rate: float = 0.04, vary_iv: bool = True):
        self._nlv = nlv
        self._day_trades_used = day_trades_used
        self._rate = rate
        # When True, IV oscillates deterministically across successive chain
        # reads so a rolling IV-rank history becomes meaningful (paper demo).
        # When False, IV is constant per symbol (stable for structural tests).
        self._vary_iv = vary_iv
        self._tick: dict[str, int] = {}

    def get_account(self) -> AccountSnapshot:
        return AccountSnapshot(
            nlv=self._nlv,
            buying_power=self._nlv,
            day_trades_used=self._day_trades_used,
        )

    def get_underlying_quote(self, symbol: str) -> UnderlyingQuote:
        base = 50.0 + _seed(symbol) % 400         # spot in [50, 450)
        return UnderlyingQuote(symbol, last=base, bid=base - 0.02, ask=base + 0.02)

    def _iv_for(self, symbol: str) -> float:
        # Base IV per symbol in roughly [0.18, 0.78].
        base = 0.18 + (_seed(symbol) % 60) / 100.0
        if not self._vary_iv:
            return base
        # Deterministic oscillation around the base so successive reads trace a
        # realistic IV path (amplitude 0.18, phase seeded per symbol).
        import math
        tick = self._tick.get(symbol, 0)
        phase = (_seed(symbol) % 100) / 100.0 * 2 * math.pi
        return round(max(0.05, 0.42 + 0.18 * math.sin(tick * 0.5 + phase)), 4)

    def _advance(self, symbol: str) -> None:
        self._tick[symbol] = self._tick.get(symbol, 0) + 1

    def get_days_to_earnings(self, symbol: str) -> int:
        # Deterministic 'days to earnings' in [3, 62].
        return 3 + _seed(symbol) % 60

    def get_option_chain(self, symbol: str, dte_target: int) -> OptionChain:
        from datetime import date, timedelta

        from ..quant.black_scholes import BSInputs, price

        spot = self.get_underlying_quote(symbol).last
        iv = self._iv_for(symbol)
        t = max(dte_target, 1) / 365.0
        expiry = (date.today() + timedelta(days=max(dte_target, 1))).isoformat()

        quotes: list[OptionQuote] = []
        # Strikes on a ~2.5% grid around spot, +/- 12 steps.
        step = max(round(spot * 0.025, 2), 0.5)
        for k in range(-12, 13):
            strike = round(spot + k * step, 2)
            if strike <= 0:
                continue
            for right, is_call in (("CALL", True), ("PUT", False)):
                mid = price(BSInputs(spot, strike, t, self._rate, iv), is_call)
                # Synthetic ~1%-of-mid spread (half = 0.5%), floored at a penny —
                # representative of the tight, liquid names on the watchlist.
                half = max(mid * 0.005, 0.01)
                quotes.append(OptionQuote(
                    symbol=symbol, expiry=expiry, strike=strike, right=right,
                    bid=round(max(mid - half, 0.01), 2),
                    ask=round(mid + half, 2),
                    open_interest=2000 + _seed(symbol + str(strike)) % 5000,
                    volume=200 + _seed(symbol + right) % 1000,
                    iv=round(iv, 4),
                ))
        chain = OptionChain(symbol=symbol,
                            underlying=self.get_underlying_quote(symbol),
                            expiries=[expiry], quotes=quotes)
        self._advance(symbol)  # next read traces the next point on the IV path
        return chain

    def review_option_order(self, symbol: str, legs: list[dict]) -> OrderReview:
        """Dry-run only. Estimates net price from the synthetic chain mids."""
        net = 0.0
        for leg in legs:
            net += leg.get("price", 0.0) * (1 if leg.get("side") == "SELL" else -1)
        return OrderReview(
            estimated_net_price=round(net, 2),
            buying_power_effect=round(abs(net) * 100, 2),
            acceptable=True,
            note="MOCK dry-run — no live order placed.",
        )
