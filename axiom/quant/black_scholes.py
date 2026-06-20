"""Black-Scholes-Merton option pricing (spec §6 compute-the-analytics).

European options on a non-dividend or continuous-dividend underlying. All vols
and rates are annualized; time is in years. These are the canonical formulas;
``tests/test_black_scholes.py`` pins them to textbook golden values.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from scipy.stats import norm

SQRT_2PI = math.sqrt(2.0 * math.pi)


@dataclass(frozen=True)
class BSInputs:
    spot: float       # underlying price S
    strike: float     # strike K
    t: float          # time to expiry in years
    rate: float       # risk-free rate r (annualized)
    vol: float        # implied volatility sigma (annualized)
    dividend: float = 0.0  # continuous dividend yield q


def _validate(args: BSInputs) -> None:
    if args.spot <= 0 or args.strike <= 0:
        raise ValueError("spot and strike must be positive")
    if args.t < 0:
        raise ValueError("time to expiry must be non-negative")
    if args.vol < 0:
        raise ValueError("volatility must be non-negative")


def d1(args: BSInputs) -> float:
    """First Black-Scholes auxiliary term."""
    _validate(args)
    num = (math.log(args.spot / args.strike)
           + (args.rate - args.dividend + 0.5 * args.vol ** 2) * args.t)
    den = args.vol * math.sqrt(args.t)
    return num / den


def d2(args: BSInputs) -> float:
    """Second Black-Scholes auxiliary term."""
    return d1(args) - args.vol * math.sqrt(args.t)


def _intrinsic(args: BSInputs, is_call: bool) -> float:
    """Discounted intrinsic value — the limit as vol or time -> 0."""
    fwd = args.spot * math.exp(-args.dividend * args.t)
    disc_k = args.strike * math.exp(-args.rate * args.t)
    return max(fwd - disc_k, 0.0) if is_call else max(disc_k - fwd, 0.0)


def price(args: BSInputs, is_call: bool) -> float:
    """Black-Scholes price of a European call or put."""
    _validate(args)
    # Degenerate cases: no time or no vol -> discounted intrinsic.
    if args.t == 0 or args.vol == 0:
        if args.t == 0:
            return (max(args.spot - args.strike, 0.0) if is_call
                    else max(args.strike - args.spot, 0.0))
        return _intrinsic(args, is_call)

    _d1 = d1(args)
    _d2 = _d1 - args.vol * math.sqrt(args.t)
    disc_s = args.spot * math.exp(-args.dividend * args.t)
    disc_k = args.strike * math.exp(-args.rate * args.t)
    if is_call:
        return disc_s * norm.cdf(_d1) - disc_k * norm.cdf(_d2)
    return disc_k * norm.cdf(-_d2) - disc_s * norm.cdf(-_d1)


def call_price(args: BSInputs) -> float:
    return price(args, is_call=True)


def put_price(args: BSInputs) -> float:
    return price(args, is_call=False)


def forward(args: BSInputs) -> float:
    """Forward price of the underlying to expiry."""
    return args.spot * math.exp((args.rate - args.dividend) * args.t)
