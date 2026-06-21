"""Snapshot real-data adapter tests (hermetic — no MCP/network)."""

import pytest

from axiom.config import load_config
from axiom.data.snapshot import (
    SnapshotRobinhoodAdapter, dte_between, option_quote_to_dict,
    spot_from_equity_quote,
)
from axiom.models import Mode
from axiom.orchestrator import run_cycle

CFG = load_config()


def _snapshot():
    # A compact, internally-consistent real-shaped snapshot for one symbol.
    quotes = []
    for strike in (15.0, 15.5, 16.0, 16.5, 17.0, 17.5, 18.0):
        quotes.append({"strike": strike, "right": "PUT", "bid": strike * 0.02,
                       "ask": strike * 0.021, "open_interest": 800, "volume": 150,
                       "iv": 0.55})
    return {
        "captured_at": "2026-06-18T20:00:00Z", "source": "robinhood_mcp",
        "risk_free_rate": 0.04,
        "account": {"nlv": 1498.0, "buying_power": 350.0, "day_trades_used": 0},
        "symbols": {
            "SOFI": {"spot": 17.91, "expiry": "2026-07-24", "dte": 36,
                     "days_to_earnings": 999, "history": [17.0 + 0.01 * i for i in range(60)],
                     "iv_rank": None, "quotes": quotes},
        },
    }


def test_adapter_serves_account_and_chain():
    ad = SnapshotRobinhoodAdapter(_snapshot())
    acct = ad.get_account()
    assert acct.nlv == 1498.0 and acct.buying_power == 350.0
    chain = ad.get_option_chain("SOFI", 35)
    assert chain.symbol == "SOFI"
    assert chain.underlying.last == 17.91
    assert all(q.iv == 0.55 for q in chain.quotes)


def test_adapter_missing_symbol_fails_closed():
    ad = SnapshotRobinhoodAdapter(_snapshot())
    assert ad.get_option_chain("NVDA", 35) is None
    assert ad.get_underlying_quote("NVDA") is None


def test_price_history_served_for_regime():
    ad = SnapshotRobinhoodAdapter(_snapshot())
    hist = ad.get_price_history("SOFI", 60)
    assert len(hist) == 60


def test_mappers_normalize_raw_robinhood_shapes():
    eq = {"quote": {"last_trade_price": "17.910000", "last_non_reg_trade_price": "17.88"}}
    assert spot_from_equity_quote(eq) == pytest.approx(17.91)
    raw = {"bid_price": "0.49", "ask_price": "0.76", "open_interest": 278,
           "volume": 174, "implied_volatility": "0.590757"}
    q = option_quote_to_dict(16.5, "put", raw)
    assert q["strike"] == 16.5 and q["right"] == "PUT"
    assert q["bid"] == pytest.approx(0.49) and q["iv"] == pytest.approx(0.590757)
    assert dte_between("2026-06-18", "2026-07-24") == 36


def test_cycle_runs_on_snapshot_real_shape(db):
    # The full decision cycle must run on snapshot data and fail closed to PASS
    # on the first capture (no IV-rank history yet) — never fabricating a rank.
    ad = SnapshotRobinhoodAdapter(_snapshot())
    report = run_cycle(db, ad, CFG, mode=Mode.PAPER, symbols=["SOFI"], manage=False)
    assert len(report.decisions) == 1
    d = report.decisions[0]
    assert d.symbol == "SOFI"
    assert d.inputs_ref.startswith("sha256:")
