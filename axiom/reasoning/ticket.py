"""Co-pilot trade ticket renderer (spec §8 / §10 alerting).

Formats a validated §8 ENTER decision into a human-readable ticket an operator
can review and place MANUALLY (the co-pilot model for non-agentic accounts). It
asserts nothing the engine didn't compute — it only presents the decision, its
economics, sizing, risk checks, and (optionally) the broker's review_option_order
dry-run. No order is placed here.
"""

from __future__ import annotations

from ..models import Decision, DecisionType


def render_ticket(decision: Decision, review: object | None = None) -> str:
    """Return a multi-line, reviewable ticket for a decision.

    For non-ENTER decisions, returns a one-line PASS/EXIT summary with the
    reason, so a cycle's full reasoning is always inspectable.
    """
    if decision.decision is not DecisionType.ENTER or decision.structure is None:
        return (f"[{decision.symbol}] {decision.decision.value} "
                f"(regime {decision.regime.value}, conv {decision.conviction}) — "
                f"{decision.thesis}")

    s = decision.structure
    e = decision.modeled_economics
    z = decision.sizing
    cb = decision.conviction_breakdown
    lines: list[str] = []
    lines.append("=" * 64)
    lines.append(f" AXIOM TRADE TICKET — {decision.mode.value}  (review & place MANUALLY)")
    lines.append("=" * 64)
    lines.append(f" {decision.symbol}  {s.type.value}   regime={decision.regime.value}"
                 f"   DTE={s.dte}")
    lines.append(f" conviction={decision.conviction}  "
                 f"[ev{cb.ev_strength} regime{cb.regime_alignment} cat{cb.catalyst_quality} "
                 f"liq{cb.liquidity} pf{cb.portfolio_fit}]")
    lines.append("-" * 64)
    lines.append(" LEGS:")
    for leg in s.legs:
        lines.append(f"   {leg.side:<4} {leg.qty} x {decision.symbol} "
                     f"{leg.expiry} {leg.strike:g} {leg.right}")
    lines.append("-" * 64)
    kind = "CREDIT" if e.credit_or_debit >= 0 else "DEBIT"
    lines.append(f" {kind} ${abs(e.credit_or_debit):.2f}/contract   "
                 f"max profit ${e.max_profit:.0f}   max loss ${e.max_loss:.0f}")
    lines.append(f" POP {e.pop:.2f}   EV(after slippage) ${e.ev_after_slippage:+.2f}   "
                 f"EV/risk {e.ev_per_dollar_risk:+.3f}")
    if e.breakevens:
        lines.append(f" breakevens: {', '.join(f'{b:g}' for b in e.breakevens)}")
    lines.append("-" * 64)
    lines.append(f" SIZE: {z.contracts} contract(s)   capital at risk "
                 f"${z.capital_at_risk:.0f}   ({z.pct_of_nlv:.1%} of NLV)")
    lines.append(f" Kelly f*={z.kelly_fraction:.3f}  applied={z.applied_fraction:.3f}")
    lines.append(f" risk checks: {'ALL PASS' if decision.risk_checks.all_pass() else 'FAILED'}")
    lines.append("-" * 64)
    lines.append(f" THESIS: {decision.thesis}")
    lines.append(f" INVALIDATION: {decision.invalidation}")
    if review is not None:
        lines.append("-" * 64)
        est = getattr(review, "estimated_net_price", None)
        bp = getattr(review, "buying_power_effect", None)
        note = getattr(review, "note", "")
        lines.append(f" BROKER DRY-RUN: net ${est}  BP effect ${bp}  {note}")
    lines.append("=" * 64)
    return "\n".join(lines)
