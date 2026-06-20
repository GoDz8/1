"""Option Greeks via closed-form Black-Scholes (spec §3/§6).

Conventions:
- delta: per $1 move in spot.
- gamma: per $1^2.
- vega: per 1.00 (100 vol-point) change in IV. Divide by 100 for per-vol-point.
- theta: per YEAR. Divide by 365 for per-calendar-day.
- rho: per 1.00 change in the rate.

``tests/test_greeks.py`` cross-checks each Greek against a finite-difference of
the BS price, which is the most robust correctness check available offline.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from scipy.stats import norm

from .black_scholes import BSInputs, d1, d2


@dataclass(frozen=True)
class Greeks:
    delta: float
    gamma: float
    vega: float    # per 1.00 vol (i.e. per 100 vol points)
    theta: float   # per year
    rho: float     # per 1.00 rate


def _pdf_d1(args: BSInputs) -> float:
    return norm.pdf(d1(args))


def delta(args: BSInputs, is_call: bool) -> float:
    disc = math.exp(-args.dividend * args.t)
    nd1 = norm.cdf(d1(args))
    return disc * (nd1 if is_call else nd1 - 1.0)


def gamma(args: BSInputs) -> float:
    disc = math.exp(-args.dividend * args.t)
    return disc * _pdf_d1(args) / (args.spot * args.vol * math.sqrt(args.t))


def vega(args: BSInputs) -> float:
    """Per 1.00 change in vol (per 100 vol points)."""
    disc = math.exp(-args.dividend * args.t)
    return args.spot * disc * _pdf_d1(args) * math.sqrt(args.t)


def theta(args: BSInputs, is_call: bool) -> float:
    """Per-year time decay (typically negative for long options)."""
    _d1 = d1(args)
    _d2 = d2(args)
    disc_s = args.spot * math.exp(-args.dividend * args.t)
    disc_k = args.strike * math.exp(-args.rate * args.t)
    term1 = -(disc_s * norm.pdf(_d1) * args.vol) / (2.0 * math.sqrt(args.t))
    if is_call:
        term2 = -args.rate * disc_k * norm.cdf(_d2)
        term3 = args.dividend * disc_s * norm.cdf(_d1)
        return term1 + term2 + term3
    term2 = args.rate * disc_k * norm.cdf(-_d2)
    term3 = -args.dividend * disc_s * norm.cdf(-_d1)
    return term1 + term2 + term3


def rho(args: BSInputs, is_call: bool) -> float:
    """Per 1.00 change in the risk-free rate."""
    disc_k = args.strike * args.t * math.exp(-args.rate * args.t)
    return disc_k * (norm.cdf(d2(args)) if is_call else -norm.cdf(-d2(args)))


def all_greeks(args: BSInputs, is_call: bool) -> Greeks:
    return Greeks(
        delta=delta(args, is_call),
        gamma=gamma(args),
        vega=vega(args),
        theta=theta(args, is_call),
        rho=rho(args, is_call),
    )
