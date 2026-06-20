"""Candidate construction + EV modeling (spec §4 Steps 2-4).

Turns a gated name + its option chain into concrete, fully-priced defined-risk
candidates with modeled fills (slippage-aware) and EV/POP/expectancy. Candidates
that fail the EV gate (spec §2.5 / §4.4) are returned flagged with a reason so
they are still logged (every evaluated candidate is persisted, spec §7).
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import Config
from ..quant.black_scholes import BSInputs
from ..quant.ev import EVResult, expected_value
from ..quant.greeks import delta as bs_delta
from ..quant.payoff import Leg, Right, Side, StructureType
from ..quant.slippage import Quote, modeled_fill_price, structure_friction
from .gating import StrategyFamily
from ..data.robinhood_mcp import OptionChain, OptionQuote

SHORT_DELTA = 0.30
LONG_DELTA = 0.20


@dataclass(frozen=True)
class Candidate:
    symbol: str
    structure_type: StructureType
    family: StrategyFamily
    legs: list[Leg]
    dte: int
    spot: float
    iv: float
    ev: EVResult
    rejected_reason: str | None = None

    @property
    def accepted(self) -> bool:
        return self.rejected_reason is None


def _quote(q: OptionQuote) -> Quote:
    return Quote(bid=q.bid, ask=q.ask)


def _select_by_delta(quotes: list[OptionQuote], right: str, spot: float, t: float,
                     rate: float, target: float) -> OptionQuote | None:
    """Pick the option of ``right`` whose |delta| is closest to ``target``."""
    is_call = right == "CALL"
    best, best_err = None, 1e9
    for q in quotes:
        if q.right != right or q.iv is None or q.iv <= 0:
            continue
        d = abs(bs_delta(BSInputs(spot, q.strike, t, rate, q.iv), is_call))
        err = abs(d - target)
        if err < best_err:
            best, best_err = q, err
    return best


def _leg_from(q: OptionQuote, side: Side, cfg: Config) -> Leg:
    premium = modeled_fill_price(_quote(q), side, cfg.slippage)
    right = Right.CALL if q.right == "CALL" else Right.PUT
    return Leg(side=side, right=right, strike=q.strike, premium=premium)


def _vertical(chain: OptionChain, right: str, credit: bool, cfg: Config,
              dte: int) -> Candidate | None:
    """Build a put/call vertical (credit or debit) and model its EV."""
    spot = chain.underlying.last
    t = max(dte, 1) / 365.0
    iv = next((q.iv for q in chain.quotes if q.iv), None)
    if iv is None:
        return None

    short_q = _select_by_delta(chain.quotes, right, spot, t, cfg.risk_free_rate, SHORT_DELTA)
    long_q = _select_by_delta(chain.quotes, right, spot, t, cfg.risk_free_rate, LONG_DELTA)
    if short_q is None or long_q is None or short_q.strike == long_q.strike:
        return None

    if credit:
        # Sell the nearer (higher-delta) strike, buy the farther wing.
        short_leg = _leg_from(short_q, Side.SELL, cfg)
        long_leg = _leg_from(long_q, Side.BUY, cfg)
        if right == "PUT":
            stype, fam = StructureType.PUT_CREDIT_SPREAD, StrategyFamily.CREDIT_SPREAD
        else:
            stype, fam = StructureType.CALL_CREDIT_SPREAD, StrategyFamily.CREDIT_SPREAD
    else:
        # Debit: buy the nearer (higher-delta), sell the farther to cheapen it.
        short_leg = _leg_from(long_q, Side.SELL, cfg)
        long_leg = _leg_from(short_q, Side.BUY, cfg)
        if right == "PUT":
            stype, fam = StructureType.PUT_DEBIT_SPREAD, StrategyFamily.DEBIT_SPREAD
        else:
            stype, fam = StructureType.CALL_DEBIT_SPREAD, StrategyFamily.DEBIT_SPREAD

    legs = [short_leg, long_leg]
    friction = structure_friction(
        [(short_leg, _quote(short_q)), (long_leg, _quote(long_q))], cfg.slippage)
    # Premium is collected at IV (in the leg prices); EV uses the realized-vol
    # forecast for the terminal distribution (the VRP edge, spec §2.1).
    rvol = iv * cfg.ev.realized_vol_ratio
    ev = expected_value(legs, spot, t, rvol, cfg.risk_free_rate, friction_cost=friction)
    return Candidate(chain.symbol, stype, fam, legs, dte, spot, iv, ev)


def build_iron_condor(chain: OptionChain, cfg: Config, dte: int) -> Candidate | None:
    pcs = _vertical(chain, "PUT", credit=True, cfg=cfg, dte=dte)
    ccs = _vertical(chain, "CALL", credit=True, cfg=cfg, dte=dte)
    if pcs is None or ccs is None:
        return None
    spot = chain.underlying.last
    t = max(dte, 1) / 365.0
    iv = pcs.iv
    legs = pcs.legs + ccs.legs
    # Reconstruct quotes for friction from the legs' strikes.
    qmap = {(q.right, q.strike): q for q in chain.quotes}
    legs_quotes = []
    for leg in legs:
        q = qmap.get((leg.right.value, leg.strike))
        if q is None:
            return None
        legs_quotes.append((leg, _quote(q)))
    friction = structure_friction(legs_quotes, cfg.slippage)
    rvol = iv * cfg.ev.realized_vol_ratio  # realized-vol forecast for EV (§2.1)
    ev = expected_value(legs, spot, t, rvol, cfg.risk_free_rate, friction_cost=friction)
    return Candidate(chain.symbol, StructureType.IRON_CONDOR,
                     StrategyFamily.IRON_CONDOR, legs, dte, spot, iv, ev)


def _apply_ev_gate(cand: Candidate, cfg: Config) -> Candidate:
    """Flag the candidate rejected if it fails the EV gate (spec §2.5/§4.4)."""
    ev = cand.ev
    reason = None
    if ev.ev_after_slippage <= cfg.ev.min_ev_after_slippage:
        reason = f"EV<=0 after slippage ({ev.ev_after_slippage:.2f})"
    elif ev.ev_per_dollar_risk < cfg.ev.min_ev_per_dollar_risk:
        reason = f"EV/risk {ev.ev_per_dollar_risk:.3f} < floor {cfg.ev.min_ev_per_dollar_risk}"
    else:
        # Friction discipline: slippage must not eat too much of expected EV.
        friction = ev.ev_gross - ev.ev_after_slippage
        if ev.ev_gross > 0 and friction / ev.ev_gross > cfg.ev.max_slippage_to_ev_ratio:
            reason = f"slippage/EV {friction / ev.ev_gross:.2f} exceeds limit"
    if reason is None:
        return cand
    return Candidate(cand.symbol, cand.structure_type, cand.family, cand.legs,
                     cand.dte, cand.spot, cand.iv, cand.ev, rejected_reason=reason)


def build_candidates(chain: OptionChain, families: tuple[StrategyFamily, ...],
                     cfg: Config, dte: int, neutral: bool = True) -> list[Candidate]:
    """Build EV-gated candidates for the allowed families on this chain."""
    out: list[Candidate] = []
    for fam in families:
        if fam is StrategyFamily.CREDIT_SPREAD:
            for right in ("PUT", "CALL"):
                c = _vertical(chain, right, credit=True, cfg=cfg, dte=dte)
                if c:
                    out.append(_apply_ev_gate(c, cfg))
        elif fam is StrategyFamily.IRON_CONDOR:
            c = build_iron_condor(chain, cfg, dte)
            if c:
                out.append(_apply_ev_gate(c, cfg))
        elif fam is StrategyFamily.DEBIT_SPREAD:
            for right in ("PUT", "CALL"):
                c = _vertical(chain, right, credit=False, cfg=cfg, dte=dte)
                if c:
                    out.append(_apply_ev_gate(c, cfg))
    return out
