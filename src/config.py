"""Loads runtime configuration from config.json."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.json"
DATA_DIR = PROJECT_ROOT / "data"

PORTFOLIO_STATE_PATH = DATA_DIR / "portfolio_state.json"
SIGNALS_STATE_PATH = DATA_DIR / "signals_state.json"
POLL_LOG_PATH = DATA_DIR / "poll_log.jsonl"
WEBULL_STATE_PATH = DATA_DIR / "webull_state.json"
WEBULL_TOKEN_DIR = DATA_DIR / "webull_token"


@dataclass(frozen=True)
class Config:
    starting_balance: float
    position_size_pct: float
    max_position_pct: float
    max_concurrent_positions: int
    daily_loss_cap_pct: float
    max_trades_per_day: int
    cooldown_minutes: int
    initial_stop_pct: float
    trailing_stop_pct: float
    poll_interval_minutes: int
    webull_mirror_enabled: bool = False


def load_config(path: Path = CONFIG_PATH) -> Config:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    return Config(**raw)
