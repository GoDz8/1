"""Strategy gating logic (spec §3 Step 3 + §2 hard rules).

Deterministic. Decides which structure *families* a name qualifies for, encoding
the §2 edge thesis so the LLM can never 'feel' its way past it:
  * IV Rank >= sell_floor + neutral/defined view -> credit spread / iron condor.
  * IV Rank < buy_ceiling + dated catalyst + direction -> debit spread.
  * Earnings within block window -> premium BUYING blocked (sells/PASS only).
  * Nothing qualifies -> empty set -> PASS.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..config import GatingConfig
from ..data.catalysts import Catalyst


class StrategyFamily(str, Enum):
    CREDIT_SPREAD = "CREDIT_SPREAD"     # put/call credit spread
    IRON_CONDOR = "IRON_CONDOR"
    DEBIT_SPREAD = "DEBIT_SPREAD"       # put/call debit spread


@dataclass(frozen=True)
class GatingInput:
    symbol: str
    iv_rank: float | None
    days_to_earnings: int | None
    catalyst: Catalyst | None
    open_interest_ok: bool
    spread_ok: bool


@dataclass(frozen=True)
class GatingResult:
    allowed: tuple[StrategyFamily, ...]
    reasons: tuple[str, ...]            # why families were allowed/blocked

    @property
    def is_empty(self) -> bool:
        return len(self.allowed) == 0


def gate(inp: GatingInput, cfg: GatingConfig) -> GatingResult:
    reasons: list[str] = []
    allowed: list[StrategyFamily] = []

    # Liquidity / data completeness first — fail closed.
    if not inp.open_interest_ok or not inp.spread_ok:
        reasons.append("liquidity/spread filter failed -> no candidates")
        return GatingResult((), tuple(reasons))
    if inp.iv_rank is None:
        reasons.append("IV rank unknown -> no candidates (fail closed)")
        return GatingResult((), tuple(reasons))

    earnings_soon = (inp.days_to_earnings is not None
                     and inp.days_to_earnings <= cfg.earnings_block_days)

    # Premium-selling path (spec §2.1 default posture).
    if inp.iv_rank >= cfg.iv_rank_sell_floor:
        allowed.append(StrategyFamily.CREDIT_SPREAD)
        allowed.append(StrategyFamily.IRON_CONDOR)
        reasons.append(f"IV rank {inp.iv_rank:.2f} >= {cfg.iv_rank_sell_floor} -> sell premium")
    else:
        reasons.append(f"IV rank {inp.iv_rank:.2f} below sell floor")

    # Premium-buying path (spec §2.2: cheap IV AND dated catalyst, never naked).
    if inp.iv_rank < cfg.iv_rank_buy_ceiling:
        if earnings_soon:
            reasons.append("earnings within block window -> premium BUY blocked (§2.3)")
        elif inp.catalyst is not None and inp.catalyst.date and inp.catalyst.directional:
            allowed.append(StrategyFamily.DEBIT_SPREAD)
            reasons.append(f"IV rank {inp.iv_rank:.2f} < {cfg.iv_rank_buy_ceiling} + dated catalyst -> debit spread")
        else:
            reasons.append("cheap IV but no dated directional catalyst -> buy forbidden (§2.2)")

    return GatingResult(tuple(allowed), tuple(reasons))
