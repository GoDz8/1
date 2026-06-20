"""Schema validation + hard-rule invariants (spec §8)."""

import pytest
from pydantic import ValidationError

from axiom.models import (
    Decision, DecisionType, ModeledEconomics, Mode, Regime, RiskChecks,
)


def _base(**over):
    data = dict(
        decision_id="d1", timestamp="2026-06-20T00:00:00Z", mode=Mode.PAPER,
        symbol="SPY", decision=DecisionType.PASS, regime=Regime.CHOP,
        inputs_ref="sha256:abc",
    )
    data.update(over)
    return Decision(**data)


def all_pass_checks():
    return RiskChecks(**{k: True for k in RiskChecks().model_dump()})


def test_pass_decision_is_valid():
    d = _base()
    assert d.decision is DecisionType.PASS


def test_enter_with_negative_ev_rejected():
    with pytest.raises(ValidationError):
        _base(
            decision=DecisionType.ENTER, conviction=80,
            modeled_economics=ModeledEconomics(ev_after_slippage=-1.0),
            risk_checks=all_pass_checks(),
        )


def test_enter_with_failed_check_rejected():
    with pytest.raises(ValidationError):
        _base(
            decision=DecisionType.ENTER, conviction=80,
            modeled_economics=ModeledEconomics(ev_after_slippage=10.0),
            risk_checks=RiskChecks(),  # all False by default
        )


def test_enter_below_conviction_floor_rejected():
    with pytest.raises(ValidationError):
        _base(
            decision=DecisionType.ENTER, conviction=50,
            modeled_economics=ModeledEconomics(ev_after_slippage=10.0),
            risk_checks=all_pass_checks(),
        )


def test_valid_enter():
    d = _base(
        decision=DecisionType.ENTER, conviction=80,
        modeled_economics=ModeledEconomics(ev_after_slippage=10.0),
        risk_checks=all_pass_checks(),
    )
    assert d.decision is DecisionType.ENTER


def test_risk_checks_all_pass_helper():
    assert all_pass_checks().all_pass() is True
    assert RiskChecks().all_pass() is False
