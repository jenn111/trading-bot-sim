"""Thin wrapper around the official webull-openapi-python-sdk, pinned to the
sandbox (paperTrade) environment only.

Authentication (App Key/App Secret, HMAC signing, optional 2FA token) is
identical between sandbox and production in Webull's OpenAPI — the only
thing that distinguishes paper from live trading is which host you call.
SANDBOX_HOST is intentionally the only host this module will ever use; it is
not read from config.json or any environment variable, so a config typo or
stray env var can't silently point this simulator at real money.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Optional

from webull.core.client import ApiClient
from webull.trade.trade_client import TradeClient

from .config import WEBULL_TOKEN_DIR

SANDBOX_HOST = "api.sandbox.webull.com"
REGION = "us"


class WebullApiError(Exception):
    def __init__(self, message: str, response=None):
        super().__init__(message)
        self.response = response


@dataclass
class OrderLeg:
    client_order_id: str
    side: str  # BUY or SELL
    stop_price: float


def new_client_order_id() -> str:
    return uuid.uuid4().hex


class WebullClient:
    def __init__(
        self,
        app_key: str,
        app_secret: str,
        account_id: Optional[str] = None,
        preferred_account_classes: tuple[str, ...] = ("INDIVIDUAL_CASH", "INDIVIDUAL_MARGIN"),
    ):
        api_client = ApiClient(app_key, app_secret, REGION)
        api_client.add_endpoint(REGION, SANDBOX_HOST)
        WEBULL_TOKEN_DIR.mkdir(parents=True, exist_ok=True)
        api_client.set_token_dir(str(WEBULL_TOKEN_DIR))
        self._trade = TradeClient(api_client)
        self._account_id = account_id
        self._preferred_account_classes = preferred_account_classes

    @property
    def account_id(self) -> str:
        if self._account_id is None:
            self._account_id = self._discover_account_id()
        return self._account_id

    def _discover_account_id(self) -> str:
        res = self._trade.account_v2.get_account_list()
        if res.status_code != 200:
            raise WebullApiError(f"get_account_list failed: HTTP {res.status_code}", res)
        accounts = res.json()
        if not accounts:
            raise WebullApiError("No accounts returned for these credentials", res)

        for acct in accounts:
            if acct.get("account_class") in self._preferred_account_classes:
                return acct["account_id"]
        return accounts[0]["account_id"]

    def place_entry_with_protective_stop(
        self,
        symbol: str,
        quantity: float,
        entry_stop_price: float,
        protective_stop_price: float,
        client_combo_order_id: str,
    ) -> tuple[OrderLeg, OrderLeg]:
        """Places a MASTER buy-stop entry with a linked protective sell-stop leg.

        The protective leg only activates once the MASTER fills (Webull's
        bracket-order semantics) — mirrors our engine's entry + initial stop
        as two independently-triggered real stop orders.
        """
        master_id = new_client_order_id()
        stop_id = new_client_order_id()
        qty_str = str(round(quantity, 4))

        new_orders = [
            {
                "client_order_id": master_id,
                "combo_type": "MASTER",
                "symbol": symbol,
                "instrument_type": "EQUITY",
                "market": "US",
                "order_type": "STOP_LOSS",
                "stop_price": str(round(entry_stop_price, 4)),
                "quantity": qty_str,
                "support_trading_session": "CORE",
                "side": "BUY",
                "entrust_type": "QTY",
                "time_in_force": "GTC",
            },
            {
                "client_order_id": stop_id,
                "combo_type": "STOP_LOSS",
                "symbol": symbol,
                "instrument_type": "EQUITY",
                "market": "US",
                "order_type": "STOP_LOSS",
                "stop_price": str(round(protective_stop_price, 4)),
                "quantity": qty_str,
                "support_trading_session": "CORE",
                "side": "SELL",
                "entrust_type": "QTY",
                "time_in_force": "GTC",
            },
        ]

        res = self._trade.order_v3.place_order(
            self.account_id, new_orders, client_combo_order_id=client_combo_order_id
        )
        if res.status_code != 200:
            raise WebullApiError(f"place_order failed: HTTP {res.status_code}: {res.text}", res)

        return (
            OrderLeg(client_order_id=master_id, side="BUY", stop_price=entry_stop_price),
            OrderLeg(client_order_id=stop_id, side="SELL", stop_price=protective_stop_price),
        )

    def replace_stop_price(self, client_order_id: str, new_stop_price: float) -> None:
        modify_orders = [
            {"client_order_id": client_order_id, "stop_price": str(round(new_stop_price, 4))}
        ]
        res = self._trade.order_v3.replace_order(self.account_id, modify_orders)
        if res.status_code != 200:
            raise WebullApiError(f"replace_order failed: HTTP {res.status_code}: {res.text}", res)

    def cancel_order(self, client_order_id: str) -> None:
        res = self._trade.order_v3.cancel_order(self.account_id, client_order_id)
        if res.status_code != 200:
            raise WebullApiError(f"cancel_order failed: HTTP {res.status_code}: {res.text}", res)

    def get_order_detail(self, client_order_id: str) -> dict:
        res = self._trade.order_v3.get_order_detail(self.account_id, client_order_id)
        if res.status_code != 200:
            raise WebullApiError(f"get_order_detail failed: HTTP {res.status_code}: {res.text}", res)
        return res.json()

    # -- Futures --------------------------------------------------------
    # Webull's futures API does not support combo/bracket orders (OTO/OCO) —
    # unlike the equity path above, entry and protective-stop are two
    # separate NORMAL orders that we place and track independently.

    _FUTURES_MONTH_CODES = "FGHJKMNQUVXZ"  # Jan..Dec

    def resolve_active_futures_contract(self, product: str, months_ahead: int = 4) -> str:
        """Finds the nearest tradeable monthly contract for `product` (e.g.
        "MGC") by probing preview_order against the sandbox until one
        validates. Contracts roll off a few days before expiry — the exact
        cutover date isn't published anywhere we can read programmatically,
        so we ask the API which symbol it'll actually accept rather than
        computing it from a hardcoded calendar rule.
        """
        from datetime import date

        today = date.today()
        year, month = today.year, today.month
        last_error: Optional[str] = None
        for _ in range(months_ahead):
            code = self._FUTURES_MONTH_CODES[month - 1]
            symbol = f"{product}{code}{year % 10}"
            probe_order = {
                "client_order_id": new_client_order_id(),
                "combo_type": "NORMAL",
                "symbol": symbol,
                "instrument_type": "FUTURES",
                "market": "US",
                "order_type": "STOP_LOSS",
                "stop_price": "1.0",
                "quantity": "1",
                "side": "BUY",
                "entrust_type": "QTY",
                "time_in_force": "GTC",
            }
            try:
                res = self._trade.order_v3.preview_order(self.account_id, [probe_order])
            except Exception as exc:  # the SDK raises (rather than returning non-200) on an invalid/expired contract
                last_error = str(exc)
            else:
                if res.status_code == 200:
                    return symbol
                last_error = res.text
            month += 1
            if month > 12:
                month = 1
                year += 1
        raise WebullApiError(
            f"Could not resolve a tradeable {product} contract in the next {months_ahead} months "
            f"(last error: {last_error})"
        )

    def place_futures_market_entry(
        self, symbol: str, side: str, quantity: int, client_order_id: str
    ) -> OrderLeg:
        order = {
            "client_order_id": client_order_id,
            "combo_type": "NORMAL",
            "symbol": symbol,
            "instrument_type": "FUTURES",
            "market": "US",
            "order_type": "MARKET",
            "quantity": str(quantity),
            "side": side,
            "entrust_type": "QTY",
            "time_in_force": "DAY",
        }
        res = self._trade.order_v3.place_order(self.account_id, [order])
        if res.status_code != 200:
            raise WebullApiError(f"place_order (futures market entry) failed: HTTP {res.status_code}: {res.text}", res)
        return OrderLeg(client_order_id=client_order_id, side=side, stop_price=0.0)

    def place_futures_stop_order(
        self, symbol: str, side: str, quantity: int, stop_price: float, client_order_id: str
    ) -> OrderLeg:
        order = {
            "client_order_id": client_order_id,
            "combo_type": "NORMAL",
            "symbol": symbol,
            "instrument_type": "FUTURES",
            "market": "US",
            "order_type": "STOP_LOSS",
            "stop_price": str(round(stop_price, 2)),
            "quantity": str(quantity),
            "side": side,
            "entrust_type": "QTY",
            "time_in_force": "GTC",
        }
        res = self._trade.order_v3.place_order(self.account_id, [order])
        if res.status_code != 200:
            raise WebullApiError(f"place_order (futures stop) failed: HTTP {res.status_code}: {res.text}", res)
        return OrderLeg(client_order_id=client_order_id, side=side, stop_price=stop_price)
