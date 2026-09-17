"""Visual dashboard: positions, trade history, cumulative P&L.

Run with:
    streamlit run dashboard.py

Reads the same data/*.json state the CLI (main.py) writes. The "Run tick
now" button fetches live prices and evaluates the strategy, same as
`python main.py tick`. Auto-refresh just re-reads saved state periodically
(e.g. state changed by a `tick` run in another terminal) — it does not fetch
new prices on its own.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from src.config import load_config
from src.engine import run_tick
from src.persistence import (
    load_portfolio,
    load_signals,
    load_webull_state,
    save_portfolio,
    save_signals,
    save_webull_state,
)
from src.webull_mirror import create_mirror

st.set_page_config(page_title="Trading Bot Sim", layout="wide")

config = load_config()

st.title("Paper Trading Simulator")

with st.sidebar:
    st.header("Controls")

    if st.button("Run tick now", type="primary", width='stretch'):
        portfolio = load_portfolio(config.starting_balance)
        signals = load_signals()
        if not signals:
            st.warning("No signals to evaluate. Add one with `python main.py add-signal ...`.")
        else:
            mirror = create_mirror(config)
            result = run_tick(config, portfolio, signals, mirror=mirror)
            save_portfolio(portfolio)
            save_signals(signals)
            if mirror is not None:
                save_webull_state(mirror.state)
            for err in result["errors"]:
                st.error(f"{err['ticker']}: {err['detail']}")
            for event in result["events"]:
                etype, ticker = event["type"], event["ticker"]
                if etype == "entry":
                    st.success(f"ENTRY {ticker} @ {event['price']:.4f}")
                elif etype == "entry_blocked":
                    st.warning(f"BLOCKED {ticker}: {'; '.join(event['reasons'])}")
                elif etype == "target_hit":
                    st.info(f"TARGET {ticker} hit {event['target']:.4f}; stop -> {event['new_stop']:.4f}")
                elif etype == "exit":
                    st.success(f"EXIT {ticker} @ {event['price']:.4f} pnl=${event['pnl']:+.2f}")
            for event in result.get("mirror_events", []):
                etype, ticker = event["type"], event["ticker"]
                if etype == "mirror_error":
                    st.error(f"WEBULL {ticker}: {event['detail']}")
                elif etype in ("mirror_entry_placed", "mirror_entry_filled", "mirror_stop_replaced", "mirror_exit_filled"):
                    st.info(f"WEBULL {ticker}: {etype.replace('mirror_', '').replace('_', ' ')}")
            if not result["events"] and not result["errors"] and not result.get("mirror_events"):
                st.caption("No triggers this tick.")
        st.rerun()

    auto_refresh = st.checkbox("Auto-refresh (5s)", value=True)
    st.caption(
        "Auto-refresh re-reads saved state; it does not poll prices on its own. "
        "Use 'Run tick now', or run `python main.py tick` in a terminal."
    )
    st.caption(
        f"Webull mirror: {'enabled' if config.webull_mirror_enabled else 'disabled'} "
        "(toggle `webull_mirror_enabled` in config.json)."
    )


@st.fragment(run_every="5s" if auto_refresh else None)
def render_dashboard() -> None:
    portfolio = load_portfolio(config.starting_balance)
    signals = load_signals()

    last_known_prices = {
        s.ticker: s.last_checked_price for s in signals if s.last_checked_price is not None
    }
    equity = portfolio.get_equity(last_known_prices)
    daily_pnl_pct = -portfolio.daily_loss_pct(last_known_prices)

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Cash", f"${portfolio.cash:,.2f}")
    col2.metric("Equity", f"${equity:,.2f}", delta=f"{equity - portfolio.starting_cash:+,.2f}")
    col3.metric("Daily P&L", f"{daily_pnl_pct:+.2%}")
    col4.metric("Trades today", f"{portfolio.trades_opened_today}/{config.max_trades_per_day}")

    st.subheader("Open Positions")
    if portfolio.positions:
        rows = []
        for ticker, pos in portfolio.positions.items():
            last_price = last_known_prices.get(ticker, pos.entry_price)
            unrealized = (last_price - pos.entry_price) * pos.quantity
            unrealized_pct = unrealized / pos.dollar_amount if pos.dollar_amount else 0.0
            rows.append(
                {
                    "Ticker": ticker,
                    "Qty": round(pos.quantity, 4),
                    "Entry": f"${pos.entry_price:.4f}",
                    "Stop": f"${pos.stop_price:.4f}",
                    "Last": f"${last_price:.4f}",
                    "Unrealized P&L": f"${unrealized:+.2f}",
                    "Unrealized %": f"{unrealized_pct:+.2%}",
                }
            )
        st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)
    else:
        st.caption("No open positions.")

    st.subheader("Trade History")
    if portfolio.trade_history:
        rows = []
        for t in portfolio.trade_history:
            rows.append(
                {
                    "Ticker": t.ticker,
                    "Entry Time": t.entry_time,
                    "Exit Time": t.exit_time,
                    "Qty": round(t.quantity, 4),
                    "Entry": f"${t.entry_price:.4f}",
                    "Exit": f"${t.exit_price:.4f}",
                    "P&L": t.pnl,
                    "P&L %": f"{t.pnl_pct:+.2%}",
                    "Reason": t.exit_reason,
                    "Targets Hit": t.targets_hit_count,
                }
            )
        history_df = pd.DataFrame(rows)
        display_df = history_df.copy()
        display_df["P&L"] = display_df["P&L"].map(lambda v: f"${v:+.2f}")
        st.dataframe(display_df, width='stretch', hide_index=True)

        total_pnl = history_df["P&L"].sum()
        st.metric("Total realized P&L", f"${total_pnl:+.2f}")

        st.subheader("Cumulative P&L")
        chart_df = history_df[["Exit Time", "P&L"]].copy()
        chart_df["Exit Time"] = pd.to_datetime(chart_df["Exit Time"])
        chart_df = chart_df.sort_values("Exit Time")
        chart_df["Cumulative P&L"] = chart_df["P&L"].cumsum()
        st.line_chart(chart_df.set_index("Exit Time")["Cumulative P&L"])
    else:
        st.caption("No closed trades yet.")

    st.subheader("Watched Signals")
    if signals:
        rows = []
        for s in signals:
            rows.append(
                {
                    "Ticker": s.ticker,
                    "Status": s.status,
                    "Entry Trigger": f"${s.entry_trigger:.4f}",
                    "Targets": ", ".join(f"${t:.2f}" for t in s.profit_targets),
                    "Targets Hit": len(s.targets_hit),
                    "Last Price": f"${s.last_checked_price:.4f}" if s.last_checked_price else "—",
                    "Last Checked": s.last_checked_at,
                }
            )
        st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)
    else:
        st.caption("No signals yet. Add one with `python main.py add-signal ...`.")

    st.subheader("Webull Mirror (paperTrade sandbox)")
    webull_state = load_webull_state()
    if webull_state:
        rows = []
        for ticker, m in webull_state.items():
            rows.append(
                {
                    "Ticker": ticker,
                    "Status": m["mirror_status"],
                    "Qty": round(m["quantity"], 4),
                    "Entry Trigger": f"${m['entry_stop_trigger']:.4f}",
                    "Entry Fill": f"${m['entry_fill_price']:.4f}" if m.get("entry_fill_price") else "—",
                    "Current Stop": f"${m['current_stop_price']:.4f}",
                    "Exit Price": f"${m['exit_price']:.4f}" if m.get("exit_price") else "—",
                    "Last Checked": m.get("last_checked_at") or "—",
                    "Last Error": m.get("last_error") or "",
                }
            )
        st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)
        st.caption(
            "Independent of our own engine — Webull triggers these orders on its own "
            "sandbox market data, so fills can differ in price/timing from our simulation above."
        )
    else:
        st.caption("No Webull-mirrored positions yet.")


render_dashboard()
