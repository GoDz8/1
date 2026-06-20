"""Market-regime metrics + labeling (spec §4 Step 1).

Deterministic Python computes the inputs (trend vs MAs, realized vs implied,
VIX level); the label is produced here mechanically. The LLM may *read* this
label but the numbers behind it come only from this module.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import KillSwitchConfig
from .models import Regime


@dataclass(frozen=True)
class RegimeSnapshot:
    vix: float | None
    price: float | None
    sma20: float | None
    sma50: float | None
    realized_vol: float | None
    implied_vol: float | None
    days_to_major_event: int | None  # nearest macro/earnings catalyst


def label_regime(snap: RegimeSnapshot,
                 ks: KillSwitchConfig | None = None) -> Regime:
    """Map a snapshot to a regime label (spec §4 Step 1).

    Rules (deterministic, conservative):
      * A dated major event within 3 days dominates -> EVENT_PENDING.
      * VIX above the de-risk threshold -> RISK_OFF.
      * Price above both MAs with rising structure -> RISK_ON.
      * Price below both MAs -> RISK_OFF.
      * Otherwise -> CHOP (the safe default for premium selling).
    """
    if snap.days_to_major_event is not None and snap.days_to_major_event <= 3:
        return Regime.EVENT_PENDING

    if ks is not None and snap.vix is not None and snap.vix >= ks.vix_spike_threshold:
        return Regime.RISK_OFF

    if snap.price is not None and snap.sma20 is not None and snap.sma50 is not None:
        if snap.price > snap.sma20 > snap.sma50:
            return Regime.RISK_ON
        if snap.price < snap.sma20 < snap.sma50:
            return Regime.RISK_OFF

    return Regime.CHOP
