"""AXIOM command-line interface (spec §7 Phase 1 item 7).

Commands:
  run-cycle   run one decision cycle (paper) and print the decisions
  run-paper   run cycles continuously, market-hours aware
  dashboard   print decisions / heat / open positions from the audit DB
  backtest    run the event-driven backtester for a strategy

The --stub-llm flag (default ON in Phase 1) uses the deterministic reasoner so
the full pipeline runs without an ANTHROPIC_API_KEY. No live-order path exists.
"""

from __future__ import annotations

import argparse
import sys
import time

from .config import load_config
from .data.robinhood_mcp import MockRobinhoodAdapter
from .logging_setup import setup_logging
from .models import DecisionType, Mode
from .orchestrator import is_market_hours, load_portfolio, run_cycle
from .storage.db import Database


def _print_decisions(report) -> None:
    print(f"\nTIER {report.tier} | {len(report.decisions)} decisions")
    print("-" * 78)
    for d in report.decisions:
        line = f"{d.symbol:<6} {d.decision.value:<5} conv={d.conviction:<3}"
        if d.decision is DecisionType.ENTER:
            e = d.modeled_economics
            line += (f" {d.structure.type.value:<18} x{d.sizing.contracts}"
                     f" EV={e.ev_after_slippage:+.2f} POP={e.pop:.2f}"
                     f" risk=${d.sizing.capital_at_risk:.0f}")
        else:
            line += f"  {d.thesis[:54]}"
        print(line)
    print("-" * 78)


def cmd_run_cycle(args) -> int:
    cfg = load_config()
    db = Database(cfg.db_path)
    adapter = MockRobinhoodAdapter(nlv=args.nlv)
    symbols = args.symbols.split(",") if args.symbols else None
    report = run_cycle(db, adapter, cfg, mode=Mode(args.mode), symbols=symbols)
    _print_decisions(report)
    db.close()
    return 0


def cmd_run_paper(args) -> int:
    cfg = load_config()
    db = Database(cfg.db_path)
    adapter = MockRobinhoodAdapter(nlv=args.nlv)
    print(f"Paper loop every {args.interval}s (Ctrl-C to stop).")
    try:
        while True:
            if args.ignore_hours or is_market_hours():
                report = run_cycle(db, adapter, cfg, mode=Mode.PAPER)
                _print_decisions(report)
            else:
                print("market closed — sleeping")
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nstopped.")
    finally:
        db.close()
    return 0


def cmd_dashboard(args) -> int:
    cfg = load_config()
    db = Database(cfg.db_path)
    rows = db.query("SELECT symbol, decision, conviction, ev_after_slippage, "
                    "capital_at_risk, timestamp FROM decisions "
                    "ORDER BY timestamp DESC LIMIT ?", (args.limit,))
    print(f"\nLast {len(rows)} decisions:")
    for r in rows:
        print(f"  {r['timestamp'][:19]} {r['symbol']:<6} {r['decision']:<5} "
              f"conv={r['conviction']} ev={r['ev_after_slippage']}")
    acct_nlv = args.nlv
    pf = load_portfolio(db, acct_nlv)
    print(f"\nOpen positions: {len(pf.positions)}  "
          f"portfolio heat: {pf.total_heat_pct():.2%} of ${acct_nlv:.0f}")
    for p in pf.positions:
        print(f"  {p.symbol:<6} {p.structure_type:<18} x{p.contracts} "
              f"max_loss=${p.max_loss_total:.0f}")
    db.close()
    return 0


def cmd_backtest(args) -> int:
    cfg = load_config()
    from .backtest.engine import backtest_put_credit_spread
    from .data.market_data import fetch_history

    df = fetch_history(args.symbol, period=args.period)
    if df is None:
        print(f"could not fetch history for {args.symbol} (network?). "
              f"Backtest needs underlying data.", file=sys.stderr)
        return 1
    closes = df["Close"].tolist()
    res = backtest_put_credit_spread(closes, cfg, dte=args.dte)
    print(f"\nBacktest {res.strategy} on {args.symbol} ({len(closes)} bars):")
    print(f"  trades={res.n_trades} win_rate={res.win_rate:.2%} "
          f"avg_pnl=${res.avg_pnl} total=${res.total_pnl}")
    print(f"  expectancy/risk={res.expectancy_per_risk} sharpe_like={res.sharpe_like}")
    print(f"  positive expectancy: {res.is_positive_expectancy()} "
          f"(NOTE: conservative lower bound — see backtest/engine.py header)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="axiom", description="AXIOM CLI (Phase 1)")
    p.add_argument("--stub-llm", action="store_true", default=True,
                   help="use deterministic reasoner (default; no API key needed)")
    sub = p.add_subparsers(dest="cmd", required=True)

    rc = sub.add_parser("run-cycle", help="run one decision cycle")
    rc.add_argument("--mode", default="PAPER", choices=[m.value for m in Mode])
    rc.add_argument("--nlv", type=float, default=300.0)
    rc.add_argument("--symbols", help="comma-separated override of the watchlist")
    rc.set_defaults(func=cmd_run_cycle)

    rp = sub.add_parser("run-paper", help="run paper cycles continuously")
    rp.add_argument("--nlv", type=float, default=300.0)
    rp.add_argument("--interval", type=int, default=300)
    rp.add_argument("--ignore-hours", action="store_true")
    rp.set_defaults(func=cmd_run_paper)

    db = sub.add_parser("dashboard", help="print decisions / heat / positions")
    db.add_argument("--limit", type=int, default=20)
    db.add_argument("--nlv", type=float, default=300.0)
    db.set_defaults(func=cmd_dashboard)

    bt = sub.add_parser("backtest", help="event-driven strategy backtest")
    bt.add_argument("--symbol", default="SPY")
    bt.add_argument("--strategy", default="put_credit_spread")
    bt.add_argument("--period", default="2y")
    bt.add_argument("--dte", type=int, default=30)
    bt.set_defaults(func=cmd_backtest)

    sm = sub.add_parser("simulate", help="Phase-2 synthetic validation run (closes the learning loop)")
    sm.add_argument("--nlv", type=float, default=8000.0)
    sm.add_argument("--days", type=int, default=252)
    sm.add_argument("--seed", type=int, default=7)
    sm.add_argument("--symbols", help="comma-separated override of the watchlist")
    sm.set_defaults(func=cmd_simulate)

    rp2 = sub.add_parser("report", help="learning report: calibration / attribution / walk-forward")
    rp2.set_defaults(func=cmd_report)

    tk = sub.add_parser("ticket", help="render the latest ENTER decision as a manual-execution ticket")
    tk.add_argument("--decision-id", help="specific decision_id (default: latest ENTER)")
    tk.set_defaults(func=cmd_ticket)

    sc = sub.add_parser("snapshot-cycle", help="run a decision cycle on a captured REAL-data snapshot")
    sc.add_argument("--file", required=True, help="path to a snapshot JSON (see data/snapshot.py)")
    sc.set_defaults(func=cmd_snapshot_cycle)
    return p


def cmd_simulate(args) -> int:
    cfg = load_config()
    db = Database(cfg.db_path)
    from .sim import run_simulation
    symbols = args.symbols.split(",") if args.symbols else None
    rep = run_simulation(db, cfg, symbols=symbols, nlv=args.nlv,
                         horizon_days=args.days, seed=args.seed)
    print(f"\nSimulation: {rep.days}d, {rep.cycles} cycles, {len(symbols or cfg.watchlist)} symbols")
    print(f"  entries={rep.entries} closes={rep.closes} wins={rep.wins} "
          f"realized=${rep.realized_pnl} final_nlv=${rep.final_nlv}")
    if rep.closes:
        print(f"  realized win rate: {rep.wins / rep.closes:.0%}")
    print(f"  calibration n={rep.calibration_n} "
          f"brier={round(rep.brier, 4) if rep.brier else 'n/a'} "
          f"log_loss={round(rep.log_loss, 4) if rep.log_loss else 'n/a'}")
    print(f"  suppressed buckets: {rep.suppressed_buckets or 'none'}")
    print("  NOTE: synthetic study with the VRP edge encoded — exercises the loop, "
          "does NOT certify live edge (see axiom/sim.py header).")
    db.close()
    return 0


def cmd_report(args) -> int:
    cfg = load_config()
    db = Database(cfg.db_path)
    from .learning.calibration import (brier_score, collect_pairs, load_calibration,
                                        log_loss, reliability_diagram)
    from .learning.attribution import compute_bucket_stats
    pairs = collect_pairs(db)
    cmap = load_calibration(db)
    print(f"\n=== Calibration (spec §11 L2) ===  resolved pairs: {len(pairs)}")
    print(f"  map n={cmap.n} shrink_weight={cmap.shrink_weight:.2f} "
          f"(applied once n>= the gate)")
    bs, ll = brier_score(pairs), log_loss(pairs)
    print(f"  Brier={round(bs, 4) if bs is not None else 'n/a'}  "
          f"log-loss={round(ll, 4) if ll is not None else 'n/a'}")
    for pm, of, c in reliability_diagram(pairs):
        print(f"    bin pred~{pm:.2f}  observed={of:.2f}  n={c}")
    print(f"\n=== Attribution (spec §11 L3) ===")
    stats = compute_bucket_stats(db, cfg.learning)
    if not stats:
        print("  (no resolved buckets yet)")
    for s in sorted(stats.values(), key=lambda x: x.shrunk_expectancy):
        flag = " SUPPRESSED" if s.suppressed else ""
        print(f"  {s.key:<34} n={s.n:<3} hit={s.hit_rate:.2f} "
              f"E/risk(shrunk)={s.shrunk_expectancy:+.3f}{flag}")
    wf = db.get_learning_state("walkforward_report")
    if wf:
        print(f"\n=== Walk-forward (spec §11 meta) ===")
        print(f"  windows={wf['n_windows']} OOS_mean={wf['out_of_sample_mean']} "
              f"degradation={wf['degradation']}")
    db.close()
    return 0


def cmd_ticket(args) -> int:
    import json

    from .models import Decision
    from .reasoning.ticket import render_ticket
    cfg = load_config()
    db = Database(cfg.db_path)
    if args.decision_id:
        row = db.query_one("SELECT payload_json FROM decisions WHERE decision_id=?",
                           (args.decision_id,))
    else:
        row = db.query_one("SELECT payload_json FROM decisions WHERE decision='ENTER' "
                           "ORDER BY timestamp DESC LIMIT 1")
    if row is None:
        print("No matching ENTER decision found. Run a cycle or simulate first.")
        db.close()
        return 1
    decision = Decision.model_validate(json.loads(row["payload_json"]))
    print(render_ticket(decision))
    db.close()
    return 0


def cmd_snapshot_cycle(args) -> int:
    from .data.snapshot import SnapshotRobinhoodAdapter, load_snapshot
    from .models import DecisionType
    cfg = load_config()
    db = Database(cfg.db_path)
    snap = load_snapshot(args.file)
    adapter = SnapshotRobinhoodAdapter(snap)
    symbols = list(snap.get("symbols", {}).keys())
    acct = adapter.get_account()
    print(f"\nSnapshot captured {snap.get('captured_at', '?')} | "
          f"NLV ${acct.nlv if acct else 0:.2f}  BP ${acct.buying_power if acct else 0:.2f}")
    report = run_cycle(db, adapter, cfg, mode=Mode.PAPER, symbols=symbols, manage=False)
    _print_decisions(report)
    # Pre-gate PREVIEW: the engine's economics computed from REAL quotes (Greeks,
    # IV, slippage). A single capture has no IV-rank history -> the gated decision
    # honestly PASSes; this preview shows the real quant the data already drives.
    from .strategy.candidates import build_candidates
    from .strategy.gating import StrategyFamily
    print("\nReal-quote economics PREVIEW (pre-gate; needs multi-day IV history to ENTER):")
    for sym in symbols:
        chain = adapter.get_option_chain(sym, 35)
        if chain is None:
            continue
        cands = build_candidates(chain, (StrategyFamily.CREDIT_SPREAD,
                                         StrategyFamily.IRON_CONDOR), cfg, 35)
        if not cands:
            print(f"  {sym:<6} (no constructible defined-risk spread from snapshot)")
        for c in cands:
            verdict = c.rejected_reason or "ACCEPTED (EV-positive)"
            print(f"  {sym:<6} {c.structure_type.value:<18} POP={c.ev.pop:.2f} "
                  f"EV(after slip)={c.ev.ev_after_slippage:+.2f} "
                  f"EV/risk={c.ev.ev_per_dollar_risk:+.3f}  maxloss=${c.ev.max_loss:.0f}  {verdict}")
    for d in report.decisions:
        if d.decision is DecisionType.ENTER:
            from .reasoning.ticket import render_ticket
            print("\n" + render_ticket(d, adapter.review_option_order(d.symbol, [])))
    db.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
