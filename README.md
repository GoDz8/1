# AXIOM — Autonomous Options Intelligence

A disciplined, EV-gated options trading system. Its objective is **risk-adjusted
profit**, not raw P&L. Default posture is **net premium seller via defined-risk
structures** (the volatility-risk-premium edge); it buys premium only when IV is
cheap *and* a dated catalyst exists, never into earnings, and it does nothing
when conviction or expected value is insufficient.

> **Status: Phase 1 + Phase 2 complete (paper/sim only).** There is **no
> live-order code** in this repository. The live path (`review_option_order` →
> `place_option_order`) is Phase 3 and gated on explicit operator approval.
> Phase 2 adds the position-management close path (§5.5), the continuous-learning
> loop (§11 Layers 2–5 + walk-forward), and a synthetic validation simulator that
> closes the loop end-to-end.

## Core principles

- **Separation of concerns.** Deterministic Python computes *every* number
  (Greeks, IV rank, EV, POP, sizing, risk checks). The LLM reasoning layer only
  reads computed context, selects among pre-validated EV-positive candidates,
  scores conviction, and emits a structured decision. **The LLM never invents a
  price, a Greek, or a fill.**
- **Fail closed.** Any missing data, failed risk check, or exception → PASS.
- **Everything is logged.** Every decision (incl. PASS/SHADOW), candidate,
  order, fill, and the input snapshot it was made from is persisted to SQLite so
  any decision is fully reconstructable.
- **The risk layer is mechanical and inviolable.** Per-trade cap, portfolio
  heat, correlation cap, Greeks bands, the no-negative-EV gate, the earnings-buy
  block, the PDT budget, and the kill switches can never be overridden by the
  reasoning layer — not even at high conviction.

## Architecture

```
orchestrator  ──>  regime ─> gating ─> candidates(+EV) ─> reasoning(LLM/stub)
                                                              │  conviction
                                                              ▼
                                       sizing ─> guard ─> paper executor
                                                              │
                                                       SQLite audit trail
data adapters: Robinhood (mock in Ph1) · yfinance · catalysts
quant engine:  black_scholes · greeks · iv_solver · iv_rank · payoff · ev
               · slippage · sizing · portfolio
```

## Layout

| Path | Purpose |
|------|---------|
| `axiom/config.py` | All CONFIG knobs; risk caps (inviolable), tiers, gates |
| `axiom/quant/` | Deterministic quant engine (BS, Greeks, IV, payoff, EV, slippage, sizing, portfolio) |
| `axiom/models.py` | Pydantic §8 decision schema + hard-rule validators |
| `axiom/storage/` | SQLite schema + DB access (full retention contract) |
| `axiom/data/` | Robinhood adapter (Protocol + mock + **snapshot real-data adapter**), yfinance, catalysts |
| `axiom/strategy/` | §3 gating + EV-gated candidate construction |
| `axiom/regime.py` | Regime metrics → label |
| `axiom/reasoning/` | LLM wrapper, prompts (§2/§4.1/§9), deterministic stub reasoner |
| `axiom/execution/` | Pre-trade guard + kill switches; **paper** executor + position manager (§5.5 close path) |
| `axiom/learning/` | §11 loop: ledger (L1), calibration (L2), attribution/adaptive gating (L3), memory (L4), post-mortems (L5), walk-forward meta-loop, refresh orchestration |
| `axiom/backtest/` | Event-driven backtester (conservative; see module header) |
| `axiom/sim.py` | Phase-2 synthetic validation simulator (closes the learning loop) |
| `axiom/orchestrator.py` | The decision cycle (Steps 1–9), market-hours aware |
| `axiom/cli.py` | `run-cycle`, `run-paper`, `dashboard`, `backtest`, `simulate`, `report`, `ticket`, `snapshot-cycle` |
| `tests/` | 159 tests: golden-value quant, FD Greeks cross-checks, isotonic/Wilson/walk-forward, risk invariants, regime, snapshot, sim/learning e2e |

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pytest -q          # all green
```

## Run (paper / offline)

```bash
# one decision cycle (deterministic stub reasoner, no API key needed)
.venv/bin/python -m axiom.cli run-cycle --mode PAPER --nlv 5000 --symbols NVDA,AAPL,MSFT

# audit dashboard
.venv/bin/python -m axiom.cli dashboard --nlv 5000

# strategy backtest (needs Yahoo Finance egress; see note below)
.venv/bin/python -m axiom.cli backtest --symbol SPY --period 2y

# Phase-2 synthetic validation: opens/manages/closes positions over a price path,
# then drives the learning loop (calibration, attribution, walk-forward)
.venv/bin/python -m axiom.cli simulate --days 504 --nlv 8000

# learning report: calibration reliability, per-bucket expectancy, walk-forward
.venv/bin/python -m axiom.cli report
```

The reasoning layer runs fully offline via a deterministic stub. To use Claude
for live reasoning, set `ANTHROPIC_API_KEY` (see `.env.example`).

## Real-data input (snapshot bridge)

The running Python process can't call the agent-side Robinhood MCP tools, so real
data flows in via a **snapshot**: the agent fetches account + chains + quotes via
the MCP read tools (`get_equity_quotes`, `get_option_chains`,
`get_option_instruments`, `get_option_quotes`, `get_equity_historicals`),
normalizes them into `data/snapshot.py`'s JSON schema, and
`SnapshotRobinhoodAdapter` serves them to the *same* RobinhoodAdapter Protocol —
so AXIOM runs its full decision cycle on **real market data** with zero engine
changes, and places no orders.

```bash
.venv/bin/python -m axiom.cli snapshot-cycle --file data/snapshot_example.json
```

Robinhood's option quotes already carry real IV + Greeks + OI, so no IV-solving
is needed. **IV rank still needs a history of IV** (spec §6): a single capture
yields `iv_rank=None` → PASS (fail closed); repeated daily captures accrue the
real history (in `learning_state`) that unlocks entries — no rank is ever
fabricated. Real captures (which include account balances) are git-ignored under
`data/snapshots/`; `data/snapshot_example.json` shows the format with placeholder
account values. (Observed live: SOFI put spreads priced **−EV after real
bid/ask slippage** — the wide quotes destroy the edge, exactly the small-account
friction the spec flags.)

## Notes & known limitations

- **Robinhood MCP** is an agent-side tool surface, not a Python library; the
  adapter is a `Protocol` with a `MockRobinhoodAdapter`, a `SimAdapter`, and a
  `SnapshotRobinhoodAdapter` (real data). An autonomous live MCP-client sidecar
  remains the Phase-3 seam.
- **Backtester** uses underlying history + Black-Scholes priced at realized vol,
  so its expectancy is a *conservative lower bound* (it understates the VRP edge)
  — use it to reject negative-expectancy strategies, not to certify an exact
  edge. Running it requires `query1/2.finance.yahoo.com` in the network egress
  allowlist.
- **Volatility risk premium** is modeled explicitly: premium is collected at IV
  while EV is computed under a realized-vol forecast (`EVConfig.realized_vol_ratio`),
  a starting hypothesis the backtester and Phase-2 paper data recalibrate.

## What's next (later phases)

Phase 3: live wiring (operator-gated; no live-order code exists yet). Phase 4:
unattended scheduling, alerting, and live recalibration against real paper-fill
data (the simulator's VRP assumption is replaced by measured realized-vs-implied).

*Options trading carries substantial risk of loss. This is a tool, not advice.*
