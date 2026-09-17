"""Renders GOLD_REPORT.md from data/gold_state.json + gold_config.json.

Run from the repo root (no arguments, no dependencies beyond stdlib):

    python scripts/render_gold_report.py

Called by .github/workflows/gold-tick.yml after every tick, so the report
committed to the repo is always current as of the last successful run —
just open GOLD_REPORT.md on GitHub to check on the strategy, no separate
agent or notification needed.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "data" / "gold_state.json"
CONFIG_PATH = ROOT / "gold_config.json"
REPORT_PATH = ROOT / "GOLD_REPORT.md"


def fmt_money(v: float) -> str:
    sign = "+" if v >= 0 else ""
    return f"{sign}${v:,.2f}"


def render(state: dict, config: dict, generated_at: datetime) -> str:
    point_value = config["point_value"]
    position = state.get("position")
    trade_history = state.get("trade_history", [])
    session_date = state.get("session_date") or "—"
    entries_today = state.get("entries_today", 0)
    max_entries = config["max_entries_per_session"]
    last_price = state.get("last_price")

    total_pnl = sum(t["pnl"] * point_value for t in trade_history)

    lines = [
        "# Gold ORB Strategy — Status Report",
        "",
        f"_Last updated: {generated_at.strftime('%Y-%m-%d %H:%M UTC')}"
        " — regenerated every 5 minutes by [gold-tick.yml]"
        "(.github/workflows/gold-tick.yml), no manual step needed._",
        "",
    ]

    if position is None and not trade_history:
        lines += [
            f"## Verdict: No trades yet",
            "",
            "The strategy hasn't opened a trade since it started running. "
            "That means no valid setup has formed — see [README.md](README.md#gold-asian-session-orb-strategy-separate-engine) "
            "for the entry rules — not that the automation is broken.",
        ]
    elif position is not None:
        lines += [f"## Verdict: Position open ({position['side'].upper()})", ""]
    else:
        last_trade = trade_history[-1]
        won = last_trade["pnl"] > 0
        lines += [
            f"## Verdict: Last trade {'won' if won else 'lost'} "
            f"({fmt_money(last_trade['pnl'] * point_value)})",
            "",
        ]

    lines += ["", "## Open Position", ""]
    if position:
        unrealized_points = (
            (last_price - position["entry_price"])
            if position["side"] == "long"
            else (position["entry_price"] - last_price)
        ) if last_price is not None else 0.0
        unrealized = unrealized_points * point_value * position["contracts"]
        lines += [
            "| Side | Contracts | Entry | Stop | Last | Unrealized P&L | Targets hit |",
            "|---|---|---|---|---|---|---|",
            f"| {position['side'].upper()} | {position['contracts']} | "
            f"${position['entry_price']:.2f} | ${position['stop_price']:.2f} | "
            f"${last_price:.2f} | {fmt_money(unrealized)} | "
            f"{len(position['targets_hit'])}/{len(position['targets'])} |",
        ]
    else:
        lines.append("No open position.")

    lines += ["", "## Recent Trades", ""]
    if trade_history:
        lines += [
            "| Exit Time | Side | Entry | Exit | P&L | Reason |",
            "|---|---|---|---|---|---|",
        ]
        for t in reversed(trade_history[-10:]):
            lines.append(
                f"| {t['exit_time']} | {t['side'].upper()} | ${t['entry_price']:.2f} | "
                f"${t['exit_price']:.2f} | {fmt_money(t['pnl'] * point_value)} | {t['exit_reason']} |"
            )
    else:
        lines.append("No closed trades yet.")

    lines += [
        "",
        "## All-Time P&L",
        "",
        f"**{fmt_money(total_pnl)}** across {len(trade_history)} closed trade(s).",
        "",
        "## Session",
        "",
        f"- Session date: {session_date}",
        f"- Entries today: {entries_today}/{max_entries}",
        f"- Last known price: {f'${last_price:.2f}' if last_price is not None else '—'}",
        "",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    report = render(state, config, datetime.now(timezone.utc))
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(f"Wrote {REPORT_PATH}")


if __name__ == "__main__":
    main()
