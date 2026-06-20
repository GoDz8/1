"""Catalyst / news hooks (spec §6: web search for catalysts, macro calendar).

Phase 1 provides the *interface* and a deterministic null/stub implementation.
The running Python process has no direct web-search tool binding here; live news
retrieval is wired alongside the real MCP/web adapters in a later phase. Per the
honesty rule (§6), missing catalyst data is reported as 'unknown' (None), and a
directional debit-spread thesis that REQUIRES a dated catalyst fails the
data-completeness check when none is found — it never fabricates one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Catalyst:
    symbol: str
    kind: str            # earnings | macro | guidance | product | none
    date: str | None     # YYYY-MM-DD if dated
    description: str
    directional: bool     # whether it implies a directional thesis


class CatalystSource(Protocol):
    def find_catalyst(self, symbol: str) -> Catalyst | None: ...


class NullCatalystSource:
    """Returns 'unknown' for everything (no fabrication). The default in Ph1."""

    def find_catalyst(self, symbol: str) -> Catalyst | None:
        return None
