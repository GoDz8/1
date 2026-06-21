"""Phase-2 validation simulator (spec §7 Phase 2).

Runs AXIOM over many synthetic sessions with a real, time-stepping price path so
positions actually OPEN, are MANAGED day by day, and CLOSE into realized
outcomes — which then feed the continuous-learning loop (§11). This is what turns
the learning layers from no-ops into a measurable calibration/attribution signal,
and lets us compare modeled vs realized expectancy before any live wiring.

Modeling honesty: the price path is generated with realized vol = IV ×
``EVConfig.realized_vol_ratio``, i.e. the path is genuinely less volatile than
implied. That is the volatility-risk-premium assumption the EV engine already
makes (§2.1); the simulator encodes it explicitly so a premium-selling edge can
actually materialize and be validated, rather than assumed. It is a *synthetic*
study, not a backtest on market data — its job is to exercise and stress the full
loop end-to-end, not to certify live edge.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np

from .config import Config
from .data.robinhood_mcp import (
    AccountSnapshot, OptionChain, OptionQuote, OrderReview, UnderlyingQuote,
)
from .execution.manage import (
    ManageAction, decide_management, expiry_pnl,
)
from .execution.paper_executor import close_paper, managed_from_row
from .learning.update import refresh_learning
from .logging_setup import get_logger
from .models import Mode
from .quant.black_scholes import BSInputs, price
from .storage.db import Database

_log = get_logger("sim")


# --------------------------------------------------------------------------- #
# Synthetic path + adapter
# --------------------------------------------------------------------------- #
def _gbm_path(s0: float, mu: float, sigma: float, days: int,
              rng: np.random.Generator) -> np.ndarray:
    dt = 1 / 252
    shocks = rng.standard_normal(days)
    rets = (mu - 0.5 * sigma ** 2) * dt + sigma * np.sqrt(dt) * shocks
    return s0 * np.exp(np.cumsum(np.concatenate([[0.0], rets])))


def _iv_path(base: float, days: int, rng: np.random.Generator) -> np.ndarray:
    """Mean-reverting (Ornstein-Uhlenbeck-ish) implied-vol path in a sane band."""
    iv = np.empty(days + 1)
    iv[0] = base
    for i in range(1, days + 1):
        iv[i] = iv[i - 1] + 0.15 * (base - iv[i - 1]) + 0.03 * rng.standard_normal()
        iv[i] = float(np.clip(iv[i], 0.08, 1.2))
    return iv


@dataclass
class _SymPath:
    spot: np.ndarray
    iv: np.ndarray


class SimAdapter:
    """RobinhoodAdapter-compatible source backed by precomputed synthetic paths.

    The path's realized vol is IV × ``realized_vol_ratio`` so the premium-selling
    edge the EV engine assumes is actually present to be harvested/validated.
    """

    def __init__(self, cfg: Config, symbols: list[str], nlv: float,
                 horizon_days: int, seed: int = 7):
        self.cfg = cfg
        self.symbols = symbols
        self.nlv = nlv
        self.horizon = horizon_days
        self.day = 0
        rng = np.random.default_rng(seed)
        self.paths: dict[str, _SymPath] = {}
        for sym in symbols:
            base_iv = float(rng.uniform(0.25, 0.6))
            realized_sigma = base_iv * cfg.ev.realized_vol_ratio
            s0 = float(rng.uniform(50, 400))
            mu = float(rng.uniform(-0.05, 0.15))
            self.paths[sym] = _SymPath(
                spot=_gbm_path(s0, mu, realized_sigma, horizon_days, rng),
                iv=_iv_path(base_iv, horizon_days, rng),
            )

    # -- time control ------------------------------------------------------- #
    def set_day(self, d: int) -> None:
        self.day = min(max(d, 0), self.horizon)

    def spot_at(self, symbol: str, day: int) -> float:
        p = self.paths[symbol].spot
        return float(p[min(day, len(p) - 1)])

    def iv_at(self, symbol: str, day: int) -> float:
        p = self.paths[symbol].iv
        return float(p[min(day, len(p) - 1)])

    # -- adapter surface ---------------------------------------------------- #
    def get_account(self) -> AccountSnapshot:
        return AccountSnapshot(nlv=self.nlv, buying_power=self.nlv, day_trades_used=0)

    def get_underlying_quote(self, symbol: str) -> UnderlyingQuote:
        s = self.spot_at(symbol, self.day)
        return UnderlyingQuote(symbol, last=s, bid=s - 0.02, ask=s + 0.02)

    def get_days_to_earnings(self, symbol: str) -> int:
        return 999  # no earnings in the synthetic study (not the focus here)

    def get_price_history(self, symbol: str, lookback: int = 60) -> list[float]:
        """Closes up to and including the current sim-day (no lookahead)."""
        path = self.paths[symbol].spot
        end = min(self.day, len(path) - 1) + 1
        start = max(0, end - lookback)
        return [float(x) for x in path[start:end]]

    def get_option_chain(self, symbol: str, dte_target: int) -> OptionChain:
        spot = self.spot_at(symbol, self.day)
        iv = self.iv_at(symbol, self.day)
        return _synth_chain(symbol, spot, iv, max(dte_target, 1), self.cfg.risk_free_rate)

    def review_option_order(self, symbol: str, legs: list[dict]) -> OrderReview:
        net = sum(l.get("price", 0.0) * (1 if l.get("side") == "SELL" else -1) for l in legs)
        return OrderReview(round(net, 2), round(abs(net) * 100, 2), True, "SIM dry-run.")


def _synth_chain(symbol: str, spot: float, iv: float, dte: int, rate: float) -> OptionChain:
    t = dte / 365.0
    expiry = (date.today() + timedelta(days=dte)).isoformat()
    step = max(round(spot * 0.025, 2), 0.5)
    quotes: list[OptionQuote] = []
    for k in range(-12, 13):
        strike = round(spot + k * step, 2)
        if strike <= 0:
            continue
        for right, is_call in (("CALL", True), ("PUT", False)):
            mid = price(BSInputs(spot, strike, t, rate, iv), is_call)
            half = max(mid * 0.005, 0.01)
            quotes.append(OptionQuote(
                symbol=symbol, expiry=expiry, strike=strike, right=right,
                bid=round(max(mid - half, 0.01), 2), ask=round(mid + half, 2),
                open_interest=5000, volume=1000, iv=round(iv, 4),
            ))
    return OptionChain(symbol, UnderlyingQuote(symbol, spot, spot - 0.02, spot + 0.02),
                       [expiry], quotes)


# --------------------------------------------------------------------------- #
# Simulation driver
# --------------------------------------------------------------------------- #
@dataclass
class SimReport:
    days: int
    cycles: int
    entries: int
    closes: int
    wins: int
    realized_pnl: float
    final_nlv: float
    brier: float | None = None
    log_loss: float | None = None
    suppressed_buckets: list[str] = field(default_factory=list)
    calibration_n: int = 0
    notes: list[str] = field(default_factory=list)


def _entry_day_map(db: Database) -> dict[str, int]:
    """position_id -> entry sim-day, persisted in learning_state across the run."""
    return db.get_learning_state("sim_entry_days") or {}


def _manage_sim_positions(db: Database, adapter: SimAdapter, cfg: Config,
                          day: int, entry_days: dict[str, int]) -> tuple[int, int, float]:
    """Mark every OPEN position at sim-day ``day`` and apply §5.5 exit rules.

    Uses simulated time (not wall-clock) so DTE actually decays across the run.
    Force-closes at expiry. Returns (closes, wins, realized_pnl).
    """
    closes = wins = 0
    realized = 0.0
    for row in db.query("SELECT * FROM positions WHERE status='OPEN'"):
        pos = managed_from_row(row)
        entry_day = entry_days.get(pos.position_id, day)
        held = day - entry_day
        dte_left = pos.dte_remaining - held  # dte_remaining is the entry DTE
        spot = adapter.spot_at(pos.symbol, day)

        if dte_left <= 0:
            # Expire at terminal payoff.
            pnl = expiry_pnl(pos, spot)
            close_paper(db, pos.position_id, pnl, "expiry",
                        invalidation=False)
            closes += 1
            wins += int(pnl > 0)
            realized += pnl * row["contracts"]
            continue

        iv = adapter.iv_at(pos.symbol, day)
        t = dte_left / 365.0
        from dataclasses import replace
        pos = replace(pos, dte_remaining=dte_left)
        d = decide_management(pos, spot, max(t, 1e-6), iv, cfg.risk_free_rate, cfg.exit_rules)
        if d.action is ManageAction.EXIT:
            close_paper(db, pos.position_id, d.pnl, d.reason, d.invalidation_triggered)
            closes += 1
            wins += int(d.pnl > 0)
            realized += d.pnl * row["contracts"]
    return closes, wins, realized


def run_simulation(db: Database, cfg: Config, symbols: list[str] | None = None,
                   nlv: float = 5000.0, horizon_days: int = 120,
                   step_days: int = 1, seed: int = 7,
                   learn_every: int = 20) -> SimReport:
    """Drive AXIOM across a synthetic horizon, closing the full learning loop."""
    from .orchestrator import run_cycle  # local import avoids a cycle

    symbols = symbols or list(cfg.watchlist)
    adapter = SimAdapter(cfg, symbols, nlv, horizon_days, seed)

    entries = closes = wins = cycles = 0
    realized_total = 0.0
    entry_days = _entry_day_map(db)
    seen_positions = set(entry_days)

    for day in range(0, horizon_days + 1, step_days):
        adapter.set_day(day)
        adapter.nlv = nlv + realized_total  # NLV compounds with realized P&L

        # Manage existing positions in SIM time first (Step 9).
        c, w, r = _manage_sim_positions(db, adapter, cfg, day, entry_days)
        closes += c
        wins += w
        realized_total += r

        # Entries: full decision cycle, wall-clock management disabled.
        report = run_cycle(db, adapter, cfg, mode=Mode.PAPER, symbols=symbols, manage=False)
        cycles += 1

        # Reconcile newly-opened positions to this sim-day.
        for row in db.query("SELECT position_id FROM positions WHERE status='OPEN'"):
            pid = row["position_id"]
            if pid not in seen_positions:
                seen_positions.add(pid)
                entry_days[pid] = day
                entries += 1
        db.put_learning_state("sim_entry_days", entry_days)

        # Periodic learning refresh (sample-gated internally).
        if learn_every and day > 0 and day % learn_every == 0:
            refresh_learning(db, cfg)

    # Final learning refresh + report.
    cmap, smap = refresh_learning(db, cfg)
    from .learning.calibration import brier_score, collect_pairs, log_loss
    pairs = collect_pairs(db)
    rep = SimReport(
        days=horizon_days, cycles=cycles, entries=entries, closes=closes,
        wins=wins, realized_pnl=round(realized_total, 2),
        final_nlv=round(nlv + realized_total, 2),
        brier=brier_score(pairs), log_loss=log_loss(pairs),
        suppressed_buckets=sorted(smap.suppressed), calibration_n=cmap.n,
    )
    if rep.closes:
        rep.notes.append(f"realized win rate {wins}/{closes} = {wins / closes:.0%}")
    _log.info("sim done: %d entries, %d closes, realized $%.2f, brier=%s",
              entries, closes, realized_total, rep.brier)
    return rep
