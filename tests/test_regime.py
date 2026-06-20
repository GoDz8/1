"""Regime labeling tests (spec §4 Step 1)."""

from axiom.config import KillSwitchConfig
from axiom.models import Regime
from axiom.regime import RegimeSnapshot, label_regime

KS = KillSwitchConfig()


def _snap(**over):
    data = dict(vix=17.0, price=100.0, sma20=98.0, sma50=95.0,
                realized_vol=0.15, implied_vol=0.2, days_to_major_event=40)
    data.update(over)
    return RegimeSnapshot(**data)


def test_event_pending_dominates():
    assert label_regime(_snap(days_to_major_event=2), KS) is Regime.EVENT_PENDING


def test_vix_spike_is_risk_off():
    assert label_regime(_snap(vix=40.0), KS) is Regime.RISK_OFF


def test_uptrend_is_risk_on():
    assert label_regime(_snap(price=100, sma20=98, sma50=95), KS) is Regime.RISK_ON


def test_downtrend_is_risk_off():
    assert label_regime(_snap(price=90, sma20=95, sma50=98), KS) is Regime.RISK_OFF


def test_default_is_chop():
    # mixed structure -> CHOP (safe default for premium selling)
    assert label_regime(_snap(price=100, sma20=95, sma50=98), KS) is Regime.CHOP


def test_missing_mas_default_chop():
    assert label_regime(_snap(sma20=None, sma50=None), KS) is Regime.CHOP
