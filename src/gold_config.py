"""Loads runtime configuration for the gold ORB strategy from gold_config.json.

Kept separate from config.py's Config (the equity-signal simulator) rather
than folded into it — contracts, R-multiples and session-anchored timing
windows are a different shape of settings than the dollar-based equity
guardrails, and this way neither config can accidentally break the other.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .config import DATA_DIR, PROJECT_ROOT

GOLD_CONFIG_PATH = PROJECT_ROOT / "gold_config.json"
GOLD_STATE_PATH = DATA_DIR / "gold_state.json"
GOLD_WEBULL_STATE_PATH = DATA_DIR / "gold_webull_state.json"


@dataclass(frozen=True)
class GoldConfig:
    yahoo_symbol: str
    webull_futures_product: str
    contracts_per_trade: int
    point_value: float
    tick_size: float
    session_open_hour_et: int
    orb15_minutes: int
    entry_window_start_minutes_after_open: int
    entry_window_end_minutes_after_open: int
    session_flatten_minutes_after_open: int
    ema_fast: int
    ema_slow: int
    bias_ema_fast: int
    bias_ema_slow: int
    swing_lookback_bars: int
    stop_buffer_ticks: int
    profit_target_r_multiples: list[float]
    trailing_lock_r: float
    max_entries_per_session: int
    poll_interval_minutes: int
    webull_futures_mirror_enabled: bool = False


def load_gold_config(path: Path = GOLD_CONFIG_PATH) -> GoldConfig:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    return GoldConfig(**raw)
