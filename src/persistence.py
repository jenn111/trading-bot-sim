"""Load/save portfolio and signal state as JSON files under data/."""
from __future__ import annotations

import json

from .config import DATA_DIR, PORTFOLIO_STATE_PATH, SIGNALS_STATE_PATH, WEBULL_STATE_PATH
from .models import Signal
from .portfolio import Portfolio


def load_portfolio(starting_cash: float) -> Portfolio:
    if not PORTFOLIO_STATE_PATH.exists():
        return Portfolio.new(starting_cash)
    with open(PORTFOLIO_STATE_PATH, "r", encoding="utf-8") as f:
        return Portfolio.from_dict(json.load(f))


def save_portfolio(portfolio: Portfolio) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(PORTFOLIO_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(portfolio.to_dict(), f, indent=2)


def load_signals() -> list[Signal]:
    if not SIGNALS_STATE_PATH.exists():
        return []
    with open(SIGNALS_STATE_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)
    return [Signal.from_dict(s) for s in raw]


def save_signals(signals: list[Signal]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(SIGNALS_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump([s.to_dict() for s in signals], f, indent=2)


def load_webull_state() -> dict:
    if not WEBULL_STATE_PATH.exists():
        return {}
    with open(WEBULL_STATE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_webull_state(state: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(WEBULL_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
