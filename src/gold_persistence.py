"""Load/save gold ORB engine state and gold Webull futures mirror state."""
from __future__ import annotations

import json

from .gold_config import GOLD_STATE_PATH, GOLD_WEBULL_STATE_PATH
from .gold_state import GoldEngineState
from .config import DATA_DIR


def load_gold_state() -> GoldEngineState:
    if not GOLD_STATE_PATH.exists():
        return GoldEngineState()
    with open(GOLD_STATE_PATH, "r", encoding="utf-8") as f:
        return GoldEngineState.from_dict(json.load(f))


def save_gold_state(state: GoldEngineState) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(GOLD_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state.to_dict(), f, indent=2)


def load_gold_webull_state() -> dict:
    if not GOLD_WEBULL_STATE_PATH.exists():
        return {}
    with open(GOLD_WEBULL_STATE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_gold_webull_state(state: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(GOLD_WEBULL_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
