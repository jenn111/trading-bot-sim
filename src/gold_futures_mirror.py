"""Mirrors the local gold ORB engine's decisions to Webull's paperTrade
(sandbox) futures account.

Same philosophy as webull_mirror.py: our local engine (gold_state.py) is the
sole decision-maker for OUR simulated P&L. This module only watches its
events and submits equivalent real orders to Webull's own paper engine,
which fills independently against Webull's own market data.

Unlike the equity mirror, Webull's futures API has no bracket/combo orders
(OTO/OCO) — entry and protective-stop are two separate NORMAL orders we
place and track by hand: place a MARKET entry, wait for it to fill, then
place a resting STOP_LOSS order as the protective stop, and trail that stop
by cancel-and-replace as local targets are hit.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from .gold_config import GoldConfig
from .webull_client import WebullApiError, WebullClient, new_client_order_id

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


_SIDE_FOR_ENTRY = {"long": "BUY", "short": "SELL"}
_SIDE_FOR_EXIT = {"long": "SELL", "short": "BUY"}


class GoldFuturesMirror:
    """Mutates `state` in place, same load/save-around-the-call pattern as
    WebullMirror. `state` holds at most one tracked position at a time,
    matching the local engine's single-position design."""

    def __init__(self, client: WebullClient, config: GoldConfig, symbol: str, state: dict):
        self.client = client
        self.config = config
        self.symbol = symbol
        self.state = state

    def handle_events(self, events: list[dict]) -> list[dict]:
        mirror_events: list[dict] = []
        for event in events:
            etype = event["type"]
            if etype == "entry":
                mirror_events.append(self._mirror_entry(event))
            elif etype == "target_hit":
                mirror_events.append(self._mirror_target_hit(event))
            elif etype == "exit":
                mirror_events.append(
                    {"type": "local_exit_noted", "detail": "our engine exited; webull mirror left running independently"}
                )
        return [e for e in mirror_events if e]

    def _mirror_entry(self, event: dict) -> Optional[dict]:
        side = event["side"]
        entry_client_order_id = new_client_order_id()
        try:
            self.client.place_futures_market_entry(
                symbol=self.symbol,
                side=_SIDE_FOR_ENTRY[side],
                quantity=event["contracts"],
                client_order_id=entry_client_order_id,
            )
        except WebullApiError as exc:
            logger.warning("Webull futures mirror entry failed: %s", exc)
            return {"type": "mirror_error", "detail": str(exc)}

        self.state.clear()
        self.state.update(
            {
                "mirror_status": "pending_entry",
                "symbol": self.symbol,
                "side": side,
                "contracts": event["contracts"],
                "entry_client_order_id": entry_client_order_id,
                "entry_fill_price": None,
                "stop_client_order_id": None,
                "current_stop_price": event["stop_price"],
                "exit_price": None,
                "created_at": utcnow().isoformat(),
                "last_checked_at": None,
                "last_error": None,
            }
        )
        return {"type": "mirror_entry_placed", "order_id": entry_client_order_id}

    def _mirror_target_hit(self, event: dict) -> Optional[dict]:
        if self.state.get("mirror_status") != "open":
            return None
        try:
            self.client.replace_stop_price(self.state["stop_client_order_id"], event["new_stop"])
        except WebullApiError as exc:
            logger.warning("Webull futures mirror stop replace failed: %s", exc)
            self.state["last_error"] = str(exc)
            return {"type": "mirror_error", "detail": str(exc)}
        self.state["current_stop_price"] = event["new_stop"]
        return {"type": "mirror_stop_replaced", "new_stop": event["new_stop"]}

    def reconcile(self) -> list[dict]:
        status = self.state.get("mirror_status")
        if status not in ("pending_entry", "open"):
            return []
        self.state["last_checked_at"] = utcnow().isoformat()
        if status == "pending_entry":
            event = self._reconcile_entry()
        else:
            event = self._reconcile_exit()
        return [event] if event else []

    def _reconcile_entry(self) -> Optional[dict]:
        try:
            detail = self.client.get_order_detail(self.state["entry_client_order_id"])
        except WebullApiError as exc:
            logger.warning("Webull futures order_detail failed for entry leg: %s", exc)
            self.state["last_error"] = str(exc)
            return None

        order = _extract_order(detail)
        if order is None:
            logger.warning("Unrecognized order_detail response shape: %r", detail)
            return None

        status = _first_present(order, _STATUS_KEYS)
        if status in _TERMINAL_UNFILLED_STATUSES:
            self.state["mirror_status"] = "error"
            self.state["last_error"] = f"entry leg ended in status {status}"
            return {"type": "mirror_entry_failed", "status": status}
        if status not in _FILLED_STATUSES:
            return None  # still pending; check again next tick

        fill_price = _first_present(order, _FILL_PRICE_KEYS)
        self.state["entry_fill_price"] = float(fill_price) if fill_price is not None else None

        stop_client_order_id = new_client_order_id()
        exit_side = _SIDE_FOR_EXIT[self.state["side"]]
        try:
            self.client.place_futures_stop_order(
                symbol=self.state["symbol"],
                side=exit_side,
                quantity=self.state["contracts"],
                stop_price=self.state["current_stop_price"],
                client_order_id=stop_client_order_id,
            )
        except WebullApiError as exc:
            logger.warning("Webull futures protective stop placement failed: %s", exc)
            self.state["mirror_status"] = "error"
            self.state["last_error"] = str(exc)
            return {"type": "mirror_error", "detail": f"entry filled but protective stop failed: {exc}"}

        self.state["stop_client_order_id"] = stop_client_order_id
        self.state["mirror_status"] = "open"
        return {"type": "mirror_entry_filled", "fill_price": self.state["entry_fill_price"]}

    def _reconcile_exit(self) -> Optional[dict]:
        try:
            detail = self.client.get_order_detail(self.state["stop_client_order_id"])
        except WebullApiError as exc:
            logger.warning("Webull futures order_detail failed for stop leg: %s", exc)
            self.state["last_error"] = str(exc)
            return None

        order = _extract_order(detail)
        if order is None:
            logger.warning("Unrecognized order_detail response shape: %r", detail)
            return None

        status = _first_present(order, _STATUS_KEYS)
        if status not in _FILLED_STATUSES:
            return None  # still open; check again next tick

        exit_price = _first_present(order, _FILL_PRICE_KEYS)
        self.state["mirror_status"] = "closed"
        self.state["exit_price"] = float(exit_price) if exit_price is not None else None
        return {"type": "mirror_exit_filled", "exit_price": self.state["exit_price"]}


def create_gold_mirror(config: GoldConfig, state: dict) -> Optional[GoldFuturesMirror]:
    """Builds a GoldFuturesMirror from config + .env, or returns None if
    mirroring is disabled or credentials/futures account aren't set up.
    Never raises — a broken Webull setup should not take down the gold
    engine's own local paper trading.
    """
    import os

    from dotenv import load_dotenv

    if not config.webull_futures_mirror_enabled:
        return None

    load_dotenv()
    app_key = os.environ.get("WEBULL_APP_KEY", "").strip()
    app_secret = os.environ.get("WEBULL_APP_SECRET", "").strip()

    if not app_key or not app_secret:
        logger.warning(
            "webull_futures_mirror_enabled is true but WEBULL_APP_KEY/WEBULL_APP_SECRET "
            "are not set in .env — skipping gold Webull mirroring this run."
        )
        return None

    try:
        client = WebullClient(app_key, app_secret, preferred_account_classes=("FUTURES",))
        symbol = client.resolve_active_futures_contract(config.webull_futures_product)
        mirror = GoldFuturesMirror(client, config, symbol, state)
        logger.info("Gold Webull futures mirror active (sandbox account: %s, contract: %s)", client.account_id, symbol)
        return mirror
    except WebullApiError as exc:
        logger.warning("Failed to initialize gold Webull futures mirror: %s", exc)
        return None
