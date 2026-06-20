# AXIOM — Autonomous Options Intelligence

A disciplined, EV-gated options trading system. Its objective is **risk-adjusted
profit**, not raw P&L. Default posture is **net premium seller via defined-risk
structures** (the volatility-risk-premium edge); it buys premium only when IV is
cheap *and* a dated catalyst exists, never into earnings, and it does nothing
when conviction or expected value is insufficient.

> **Status: Phase 1 complete (paper only).** There is **no live-order code** in
> this repository. The live path (`review_option_order` → `place_option_order`)
> is Phase 3 and gated on explicit operator approval. See the build plan in the
> spec (§7) for phasing.

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
| `axiom/data/` | Robinhood adapter (Protocol + mock), yfinance, catalysts |
| `axiom/strategy/` | §3 gating + EV-gated candidate construction |
| `axiom/regime.py` | Regime metrics → label |
| `axiom/reasoning/` | LLM wrapper, prompts (§2/§4.1/§9), deterministic stub reasoner |
| `axiom/execution/` | Pre-trade guard + kill switches; **paper** executor only |
| `axiom/learning/` | Layer-1 forecast/outcome ledger (capture) |
| `axiom/backtest/` | Event-driven backtester (conservative; see module header) |
| `axiom/orchestrator.py` | The decision cycle (Steps 1–9), market-hours aware |
| `axiom/cli.py` | `run-cycle`, `run-paper`, `dashboard`, `backtest` |
| `tests/` | 111 tests: golden-value quant, FD Greeks cross-checks, risk invariants, e2e |

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
```

The reasoning layer runs fully offline via a deterministic stub. To use Claude
for live reasoning, set `ANTHROPIC_API_KEY` (see `.env.example`).

## Notes & known limitations

- **Robinhood MCP** is an agent-side tool surface, not a Python library; live
  account auth is not wired in Phase 1. The adapter is a `Protocol` with a
  deterministic `MockRobinhoodAdapter`; a real MCP-client implementation is the
  Phase-3 integration seam.
- **Backtester** uses underlying history + Black-Scholes priced at realized vol,
  so its expectancy is a *conservative lower bound* (it understates the VRP edge)
  — use it to reject negative-expectancy strategies, not to certify an exact
  edge. Running it requires `query1/2.finance.yahoo.com` in the network egress
  allowlist.
- **Volatility risk premium** is modeled explicitly: premium is collected at IV
  while EV is computed under a realized-vol forecast (`EVConfig.realized_vol_ratio`),
  a starting hypothesis the backtester and Phase-2 paper data recalibrate.

## What's next (later phases)

Phase 2: paper-fill calibration & strategy validation. Phase 3: live wiring
(operator-gated). Phase 4: unattended scheduling, alerting, learning Layers 2–5
and the walk-forward meta-loop.

*Options trading carries substantial risk of loss. This is a tool, not advice.*
