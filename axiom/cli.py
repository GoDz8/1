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
    return p


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
