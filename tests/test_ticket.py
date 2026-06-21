"""Trade-ticket renderer tests (co-pilot manual-execution artifact)."""

from axiom.models import (
    Decision, DecisionType, Leg, Mode, ModeledEconomics, Regime, RiskChecks,
    Sizing, Structure, StructureKind,
)
from axiom.reasoning.ticket import render_ticket


def _enter():
    return Decision(
        decision_id="d1", timestamp="2026-06-21T00:00:00Z", mode=Mode.PAPER,
        symbol="NVDA", decision=DecisionType.ENTER, regime=Regime.CHOP,
        inputs_ref="sha256:x", conviction=78,
        structure=Structure(type=StructureKind.PUT_CREDIT_SPREAD, dte=35, legs=[
            Leg(side="SELL", right="PUT", strike=95, expiry="2026-07-26", qty=1),
            Leg(side="BUY", right="PUT", strike=90, expiry="2026-07-26", qty=1),
        ]),
        modeled_economics=ModeledEconomics(
            credit_or_debit=100.0, max_profit=100.0, max_loss=400.0, pop=0.71,
            ev_after_slippage=22.0, ev_per_dollar_risk=0.055, breakevens=[94.0]),
        sizing=Sizing(kelly_fraction=0.13, applied_fraction=0.02, contracts=2,
                      capital_at_risk=800.0, pct_of_nlv=0.16),
        thesis="rich IV, sell premium", invalidation="breach short strike",
        risk_checks=RiskChecks(**{k: True for k in RiskChecks().model_dump()}),
    )


def test_enter_ticket_contains_key_fields():
    t = render_ticket(_enter())
    assert "TRADE TICKET" in t
    assert "PUT_CREDIT_SPREAD" in t
    assert "SELL 1 x NVDA 2026-07-26 95 PUT" in t
    assert "max loss $400" in t
    assert "ALL PASS" in t
    assert "place MANUALLY" in t


def test_enter_ticket_shows_review_when_given():
    class Review:
        estimated_net_price = 1.0
        buying_power_effect = 400.0
        note = "dry-run"
    t = render_ticket(_enter(), review=Review())
    assert "BROKER DRY-RUN" in t
    assert "400.0" in t


def test_pass_decision_one_line():
    d = Decision(
        decision_id="d2", timestamp="2026-06-21T00:00:00Z", mode=Mode.PAPER,
        symbol="AAPL", decision=DecisionType.PASS, regime=Regime.CHOP,
        inputs_ref="sha256:y", thesis="PASS: no edge",
    )
    t = render_ticket(d)
    assert t.startswith("[AAPL] PASS")
    assert "no edge" in t
