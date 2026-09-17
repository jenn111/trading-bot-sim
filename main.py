"""CLI entrypoint for the paper-trading simulator core engine.

Usage:
    python main.py add-signal --ticker SUNE --ref-price 3.10 --entry 3.34-3.38 \\
        --major-breakout 3.59 --targets 3.75,4.10,4.50 [--dip-levels 2.95,2.80]
    python main.py signals
    python main.py tick
    python main.py status
    python main.py history
"""
from __future__ import annotations

import argparse
import sys

from src.config import load_config
from src.engine import run_tick
from src.gold_config import load_gold_config
from src.gold_engine import run_gold_tick
from src.gold_futures_mirror import create_gold_mirror
from src.gold_persistence import load_gold_state, load_gold_webull_state, save_gold_state, save_gold_webull_state
from src.models import Signal
from src.persistence import load_portfolio, load_signals, save_portfolio, save_signals, save_webull_state
from src.webull_mirror import create_mirror


def cmd_add_signal(args: argparse.Namespace) -> None:
    if "-" in args.entry:
        low_s, high_s = args.entry.split("-", 1)
        entry_low, entry_trigger = float(low_s), float(high_s)
    else:
        entry_low, entry_trigger = None, float(args.entry)

    targets = [float(t) for t in args.targets.split(",") if t.strip()]
    dip_levels = [float(d) for d in args.dip_levels.split(",")] if args.dip_levels else []

    signal = Signal(
        ticker=args.ticker.upper(),
        reference_price=args.ref_price,
        entry_trigger=entry_trigger,
        entry_low=entry_low,
        major_breakout_level=args.major_breakout,
        profit_targets=sorted(targets),
        dip_levels=dip_levels,
    )

    signals = load_signals()
    signals.append(signal)
    save_signals(signals)

    range_str = f"{entry_low}-{entry_trigger}" if entry_low is not None else str(entry_trigger)
    print(f"Added signal: {signal.ticker} entry={range_str} (trigger={entry_trigger}) targets={targets}")


def cmd_signals(_args: argparse.Namespace) -> None:
    signals = load_signals()
    if not signals:
        print("No signals.")
        return
    for s in signals:
        print(
            f"{s.ticker:<8} status={s.status:<8} trigger={s.entry_trigger:<8} "
            f"targets={s.profit_targets} targets_hit={s.targets_hit} "
            f"last_price={s.last_checked_price}"
        )


def cmd_tick(_args: argparse.Namespace) -> None:
    config = load_config()
    portfolio = load_portfolio(config.starting_balance)
    signals = load_signals()

    if not signals:
        print("No signals to evaluate. Add one with `python main.py add-signal ...` first.")
        return

    mirror = create_mirror(config)
    result = run_tick(config, portfolio, signals, mirror=mirror)

    for error in result["errors"]:
        print(f"[error] {error['ticker']}: {error['detail']}")

    if not result["events"]:
        print("No triggers this tick.")

    for event in result["events"]:
        etype = event["type"]
        ticker = event["ticker"]
        if etype == "entry":
            print(
                f"[ENTRY] {ticker} @ {event['price']:.4f} "
                f"size=${event['dollar_amount']:.2f} stop={event['stop_price']:.4f}"
            )
        elif etype == "entry_blocked":
            print(f"[BLOCKED] {ticker} entry blocked: {'; '.join(event['reasons'])}")
        elif etype == "target_hit":
            print(
                f"[TARGET] {ticker} hit {event['target']:.4f} @ {event['price']:.4f}; "
                f"stop moved to {event['new_stop']:.4f}"
            )
        elif etype == "exit":
            print(
                f"[EXIT] {ticker} @ {event['price']:.4f} "
                f"pnl=${event['pnl']:.2f} ({event['pnl_pct']:+.2%})"
            )

    for event in result.get("mirror_events", []):
        etype = event["type"]
        ticker = event["ticker"]
        if etype == "mirror_entry_placed":
            print(f"[WEBULL] {ticker} bracket order placed (master={event['master_order_id']})")
        elif etype == "mirror_entry_filled":
            fill = event["fill_price"]
            print(f"[WEBULL] {ticker} master leg filled @ {fill}" if fill else f"[WEBULL] {ticker} master leg filled")
        elif etype == "mirror_stop_replaced":
            print(f"[WEBULL] {ticker} protective stop replaced -> {event['new_stop']:.4f}")
        elif etype == "mirror_exit_filled":
            print(f"[WEBULL] {ticker} protective stop filled @ {event['exit_price']}")
        elif etype == "mirror_error":
            print(f"[WEBULL error] {ticker}: {event['detail']}")
        elif etype == "local_exit_noted":
            print(f"[WEBULL] {ticker}: {event['detail']}")

    save_portfolio(portfolio)
    save_signals(signals)
    if mirror is not None:
        save_webull_state(mirror.state)


def cmd_status(_args: argparse.Namespace) -> None:
    config = load_config()
    portfolio = load_portfolio(config.starting_balance)
    signals = load_signals()

    last_known_prices = {
        s.ticker: s.last_checked_price for s in signals if s.last_checked_price is not None
    }
    equity = portfolio.get_equity(last_known_prices)

    print(f"Cash:          ${portfolio.cash:,.2f}")
    print(f"Equity (est.): ${equity:,.2f}  (using last-known prices, not live)")
    print(f"Trades today:  {portfolio.trades_opened_today}/{config.max_trades_per_day}")
    print(f"Daily P&L:     {portfolio.daily_loss_pct(last_known_prices) * -1:+.2%}")
    print()
    if not portfolio.positions:
        print("No open positions.")
    else:
        print("Open positions:")
        for ticker, pos in portfolio.positions.items():
            last_price = last_known_prices.get(ticker, pos.entry_price)
            unrealized = (last_price - pos.entry_price) * pos.quantity
            print(
                f"  {ticker:<8} qty={pos.quantity:.4f} entry={pos.entry_price:.4f} "
                f"stop={pos.stop_price:.4f} last={last_price:.4f} "
                f"unrealized=${unrealized:+.2f}"
            )


def cmd_history(_args: argparse.Namespace) -> None:
    config = load_config()
    portfolio = load_portfolio(config.starting_balance)
    if not portfolio.trade_history:
        print("No closed trades yet.")
        return
    total_pnl = 0.0
    for t in portfolio.trade_history:
        total_pnl += t.pnl
        print(
            f"{t.ticker:<8} entry={t.entry_price:.4f} exit={t.exit_price:.4f} "
            f"qty={t.quantity:.4f} pnl=${t.pnl:+.2f} ({t.pnl_pct:+.2%}) "
            f"reason={t.exit_reason} targets_hit={t.targets_hit_count}"
        )
    print(f"\nTotal realized P&L: ${total_pnl:+.2f}")


def cmd_gold_tick(_args: argparse.Namespace) -> None:
    cfg = load_gold_config()
    state = load_gold_state()
    mirror_state = load_gold_webull_state()
    mirror = create_gold_mirror(cfg, mirror_state)

    result = run_gold_tick(cfg, state, mirror=mirror)

    for error in result["errors"]:
        print(f"[error] {error['detail']}")

    if not result["events"]:
        print(f"[{result.get('now_et', '?')}] No triggers this tick.")

    for event in result["events"]:
        etype = event["type"]
        if etype == "entry":
            print(
                f"[ENTRY] {event['side'].upper()} @ {event['price']:.2f} "
                f"contracts={event['contracts']} stop={event['stop_price']:.2f} "
                f"targets={[round(t, 2) for t in event['targets']]}"
            )
        elif etype == "target_hit":
            print(f"[TARGET] {event['side']} hit {event['target']:.2f} @ {event['price']:.2f}; stop moved to {event['new_stop']:.2f}")
        elif etype == "exit":
            print(f"[EXIT] {event['side']} @ {event['price']:.2f} pnl={event['pnl_points']:+.2f} points ({event['reason']})")

    for event in result.get("mirror_events", []):
        etype = event["type"]
        if etype == "mirror_entry_placed":
            print(f"[WEBULL] futures entry order placed (id={event['order_id']})")
        elif etype == "mirror_entry_filled":
            fill = event["fill_price"]
            print(f"[WEBULL] entry filled @ {fill}" if fill else "[WEBULL] entry filled")
        elif etype == "mirror_stop_replaced":
            print(f"[WEBULL] protective stop replaced -> {event['new_stop']:.2f}")
        elif etype == "mirror_exit_filled":
            print(f"[WEBULL] protective stop filled @ {event['exit_price']}")
        elif etype == "mirror_error":
            print(f"[WEBULL error] {event['detail']}")
        elif etype == "local_exit_noted":
            print(f"[WEBULL] {event['detail']}")

    save_gold_state(state)
    if mirror is not None:
        save_gold_webull_state(mirror.state)


def cmd_gold_status(_args: argparse.Namespace) -> None:
    cfg = load_gold_config()
    state = load_gold_state()

    print(f"Session date:  {state.session_date or '(none yet)'}")
    print(f"Entries today: {state.entries_today}/{cfg.max_entries_per_session}")
    print()
    if state.position is None:
        print("No open position.")
    else:
        pos = state.position
        last = state.last_price if state.last_price is not None else pos.entry_price
        unrealized_points = (last - pos.entry_price) if pos.side == "long" else (pos.entry_price - last)
        print(
            f"{pos.side.upper():<6} contracts={pos.contracts} entry={pos.entry_price:.2f} "
            f"stop={pos.stop_price:.2f} last={last:.2f} "
            f"unrealized={unrealized_points * cfg.point_value * pos.contracts:+.2f} "
            f"targets_hit={len(pos.targets_hit)}/{len(pos.targets)}"
        )


def cmd_gold_history(_args: argparse.Namespace) -> None:
    cfg = load_gold_config()
    state = load_gold_state()
    if not state.trade_history:
        print("No closed gold trades yet.")
        return
    total_pnl = 0.0
    for t in state.trade_history:
        dollar_pnl = t.pnl * cfg.point_value
        total_pnl += dollar_pnl
        print(
            f"{t.side.upper():<6} entry={t.entry_price:.2f} exit={t.exit_price:.2f} "
            f"contracts={t.contracts} pnl=${dollar_pnl:+.2f} reason={t.exit_reason} "
            f"targets_hit={t.targets_hit_count}"
        )
    print(f"\nTotal realized P&L: ${total_pnl:+.2f}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Paper-trading simulator")
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add-signal", help="Add a new signal to watch")
    p_add.add_argument("--ticker", required=True)
    p_add.add_argument("--ref-price", type=float, required=True)
    p_add.add_argument("--entry", required=True, help="Single level (3.38) or range (3.34-3.38)")
    p_add.add_argument("--major-breakout", type=float, default=None)
    p_add.add_argument("--targets", required=True, help="Comma-separated, e.g. 3.75,4.10,4.50")
    p_add.add_argument("--dip-levels", default=None, help="Comma-separated, reference only")
    p_add.set_defaults(func=cmd_add_signal)

    p_signals = sub.add_parser("signals", help="List all signals")
    p_signals.set_defaults(func=cmd_signals)

    p_tick = sub.add_parser("tick", help="Fetch live prices and evaluate all active signals once")
    p_tick.set_defaults(func=cmd_tick)

    p_status = sub.add_parser("status", help="Show portfolio status")
    p_status.set_defaults(func=cmd_status)

    p_history = sub.add_parser("history", help="Show closed trade history")
    p_history.set_defaults(func=cmd_history)

    p_gold_tick = sub.add_parser("gold-tick", help="Evaluate the gold Asian-session ORB strategy once")
    p_gold_tick.set_defaults(func=cmd_gold_tick)

    p_gold_status = sub.add_parser("gold-status", help="Show gold ORB engine status")
    p_gold_status.set_defaults(func=cmd_gold_status)

    p_gold_history = sub.add_parser("gold-history", help="Show closed gold ORB trade history")
    p_gold_history.set_defaults(func=cmd_gold_history)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main(sys.argv[1:])
