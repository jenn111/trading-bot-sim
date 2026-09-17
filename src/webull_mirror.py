"""Translates our engine's own strategy events into mirrored orders on
Webull's paperTrade (sandbox) account.

Our engine (strategy.py / portfolio.py) remains the sole decision-maker and
source of truth for OUR simulated portfolio and P&L. This module never reads
back into our portfolio — it only watches our engine's events and submits
the equivalent real stop orders to Webull's own paper-trading engine, which
then triggers independently on Webull's own market data. The two engines'
fills can and will drift apart in price/timing; that's expected, not a bug.

Order status field names below (status, average fill price) are inferred
from the SDK's OrderStatus enum and sample code but not yet confirmed
against a real sandbox response — reconcile() is written to fail soft
(log + skip) if a field isn't where expected, rather than crash the tick.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional

from dotenv import load_dotenv

from .config import Config
from .persistence import load_webull_state
from .webull_client import WebullApiError, WebullClient

logger = logging.getLogger(__name__)

_STATUS_KEYS = ("status", "order_status")
_FILL_PRICE_KEYS = (
    "avg_filled_price",
    "average_fill_price",
    "avg_fill_price",
    "filled_avg_price",
    "fill_price",
)
_FILLED_STATUSES = {"FILLED"}
_TERMINAL_UNFILLED_STATUSES = {"CANCELLED", "FAILED"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _first_present(d: dict, keys: tuple[str, ...]) -> Optional[object]:
    for key in keys:
        if key in d and d[key] is not None:
            return d[key]
    return None


def _extract_order(detail: dict) -> Optional[dict]:
    orders = detail.get("orders")
    if orders:
        return orders[0]
    return detail if "client_order_id" in detail else None


class WebullMirror:
    """Mutates `state` in place and never touches disk itself — same pattern as
    Portfolio/Signal. The caller loads state via load_webull_state() before use
    and persists it via save_webull_state() after, same as portfolio/signals.
    """

    def __init__(self, client: WebullClient, config: Config, state: dict):
        self.client = client
        self.config = config
        self.state = state

    def handle_events(self, events: list[dict], portfolio, signals: list) -> list[dict]:
        """Reacts to this tick's strategy events. Returns log-friendly mirror events."""
        signals_by_ticker = {s.ticker: s for s in signals}
        mirror_events: list[dict] = []
        for event in events:
            etype = event["type"]
            ticker = event["ticker"]
            if etype == "entry":
                signal = signals_by_ticker.get(ticker)
                mirror_events.append(self._mirror_entry(ticker, portfolio, signal))
            elif etype == "target_hit":
                mirror_events.append(self._mirror_target_hit(ticker, event["new_stop"]))
            elif etype == "exit":
                # Our engine closed based on its own (Yahoo) price feed. We deliberately
                # do NOT cancel or force-close the Webull side — it keeps running
                # independently against Webull's own market data for comparison.
                mirror_events.append(
                    {"type": "local_exit_noted", "ticker": ticker, "detail": "our engine exited; webull mirror left running independently"}
                )
        return [e for e in mirror_events if e]

    def _mirror_entry(self, ticker: str, portfolio, signal) -> Optional[dict]:
        position = portfolio.positions.get(ticker)
        if position is None:
            return {"type": "mirror_error", "ticker": ticker, "detail": "no local position found to mirror"}
        if signal is None:
            return {"type": "mirror_error", "ticker": ticker, "detail": "no matching signal found to mirror"}

        try:
            master_leg, stop_leg = self.client.place_entry_with_protective_stop(
                symbol=ticker,
                quantity=position.quantity,
                entry_stop_price=signal.entry_trigger,
                protective_stop_price=position.stop_price,
                client_combo_order_id=f"{ticker}-{int(position.entry_time.timestamp())}",
            )
        except WebullApiError as exc:
            logger.warning("Webull mirror entry failed for %s: %s", ticker, exc)
            return {"type": "mirror_error", "ticker": ticker, "detail": str(exc)}

        self.state[ticker] = {
            "account_id": self.client.account_id,
            "master_client_order_id": master_leg.client_order_id,
            "stop_client_order_id": stop_leg.client_order_id,
            "mirror_status": "pending_entry",
            "quantity": position.quantity,
            "entry_stop_trigger": signal.entry_trigger,
            "current_stop_price": position.stop_price,
            "entry_fill_price": None,
            "trued_up": False,
            "exit_price": None,
            "exit_time": None,
            "created_at": utcnow().isoformat(),
            "last_checked_at": None,
            "last_error": None,
        }
        return {
            "type": "mirror_entry_placed",
            "ticker": ticker,
            "master_order_id": master_leg.client_order_id,
            "stop_order_id": stop_leg.client_order_id,
        }

    def _mirror_target_hit(self, ticker: str, new_stop: float) -> Optional[dict]:
        mirror = self.state.get(ticker)
        if mirror is None or mirror["mirror_status"] not in ("pending_entry", "open"):
            return None
        try:
            self.client.replace_stop_price(mirror["stop_client_order_id"], new_stop)
        except WebullApiError as exc:
            logger.warning("Webull mirror stop replace failed for %s: %s", ticker, exc)
            mirror["last_error"] = str(exc)
            return {"type": "mirror_error", "ticker": ticker, "detail": str(exc)}

        mirror["current_stop_price"] = new_stop
        return {"type": "mirror_stop_replaced", "ticker": ticker, "new_stop": new_stop}

    def reconcile(self) -> list[dict]:
        """Polls Webull order status for every tracked mirror position.

        Confirms entry fills (trueing up the protective stop to the actual
        fill price, matching our engine's own "3% below fill" rule) and
        detects when Webull's own protective stop has filled independently.
        """
        events: list[dict] = []
        for ticker, mirror in self.state.items():
            mirror["last_checked_at"] = utcnow().isoformat()

            if mirror["mirror_status"] == "pending_entry":
                events.append(self._reconcile_entry(ticker, mirror))
            elif mirror["mirror_status"] == "open":
                events.append(self._reconcile_exit(ticker, mirror))

        return [e for e in events if e]

    def _reconcile_entry(self, ticker: str, mirror: dict) -> Optional[dict]:
        try:
            detail = self.client.get_order_detail(mirror["master_client_order_id"])
        except WebullApiError as exc:
            logger.warning("Webull order_detail failed for %s master leg: %s", ticker, exc)
            mirror["last_error"] = str(exc)
            return None

        order = _extract_order(detail)
        if order is None:
            logger.warning("Unrecognized order_detail response shape for %s: %r", ticker, detail)
            return None

        status = _first_present(order, _STATUS_KEYS)
        if status in _TERMINAL_UNFILLED_STATUSES:
            mirror["mirror_status"] = "error"
            mirror["last_error"] = f"master leg ended in status {status}"
            return {"type": "mirror_entry_failed", "ticker": ticker, "status": status}

        if status not in _FILLED_STATUSES:
            return None  # still pending; check again next tick

        fill_price = _first_present(order, _FILL_PRICE_KEYS)
        mirror["mirror_status"] = "open"
        if fill_price is None:
            logger.warning(
                "Master leg for %s filled but no fill price field found in response: %r", ticker, order
            )
            return {"type": "mirror_entry_filled", "ticker": ticker, "fill_price": None}

        fill_price = float(fill_price)
        mirror["entry_fill_price"] = fill_price

        # Never lower a stop that our engine's own target-hit trailing has already
        # raised past this (e.g. a gap that crossed the entry AND one or more
        # profit targets in the same tick) — true-up only corrects for fill-price
        # uncertainty, it never undoes trailing that already happened.
        fill_based_stop = fill_price * (1 - self.config.initial_stop_pct)
        true_stop = max(mirror["current_stop_price"], fill_based_stop)
        if abs(true_stop - mirror["current_stop_price"]) > 1e-9:
            try:
                self.client.replace_stop_price(mirror["stop_client_order_id"], true_stop)
                mirror["current_stop_price"] = true_stop
                mirror["trued_up"] = True
            except WebullApiError as exc:
                logger.warning("Webull stop true-up failed for %s: %s", ticker, exc)
                mirror["last_error"] = str(exc)

        return {"type": "mirror_entry_filled", "ticker": ticker, "fill_price": fill_price}

    def _reconcile_exit(self, ticker: str, mirror: dict) -> Optional[dict]:
        try:
            detail = self.client.get_order_detail(mirror["stop_client_order_id"])
        except WebullApiError as exc:
            logger.warning("Webull order_detail failed for %s stop leg: %s", ticker, exc)
            mirror["last_error"] = str(exc)
            return None

        order = _extract_order(detail)
        if order is None:
            logger.warning("Unrecognized order_detail response shape for %s: %r", ticker, detail)
            return None

        status = _first_present(order, _STATUS_KEYS)
        if status not in _FILLED_STATUSES:
            return None  # still open; check again next tick

        exit_price = _first_present(order, _FILL_PRICE_KEYS)
        mirror["mirror_status"] = "closed"
        mirror["exit_price"] = float(exit_price) if exit_price is not None else None
        mirror["exit_time"] = utcnow().isoformat()
        return {"type": "mirror_exit_filled", "ticker": ticker, "exit_price": mirror["exit_price"]}


def create_mirror(config: Config) -> Optional[WebullMirror]:
    """Builds a WebullMirror from config + .env, or returns None if mirroring
    is disabled or credentials aren't set up yet. Never raises — a broken or
    missing Webull setup should not take down the rest of the simulator.
    """
    if not config.webull_mirror_enabled:
        return None

    load_dotenv()
    app_key = os.environ.get("WEBULL_APP_KEY", "").strip()
    app_secret = os.environ.get("WEBULL_APP_SECRET", "").strip()
    account_id = os.environ.get("WEBULL_ACCOUNT_ID", "").strip() or None

    if not app_key or not app_secret:
        logger.warning(
            "webull_mirror_enabled is true but WEBULL_APP_KEY/WEBULL_APP_SECRET "
            "are not set in .env — skipping Webull mirroring this run."
        )
        return None

    try:
        client = WebullClient(app_key, app_secret, account_id=account_id)
        mirror = WebullMirror(client, config, load_webull_state())
        logger.info("Webull mirror active (sandbox account: %s)", client.account_id)
        return mirror
    except WebullApiError as exc:
        logger.warning("Failed to initialize Webull mirror: %s", exc)
        return None
