"""Ties gold market data + gold_orb strategy + gold engine state + the
optional Webull futures mirror together for one evaluation pass."""
from __future__ import annotations

from typing import Optional

from . import gold_market_data
from .gold_config import GoldConfig
from .gold_futures_mirror import GoldFuturesMirror
from .gold_state import GoldEngineState, process_gold_tick
from .logging_utils import log_event
from .market_data import MarketDataError


def run_gold_tick(
    cfg: GoldConfig,
    state: GoldEngineState,
    mirror: Optional[GoldFuturesMirror] = None,
) -> dict:
    now = gold_market_data.now_et()

    try:
        bars_5m = gold_market_data.get_5m_bars(cfg.yahoo_symbol)
        bars_1h = gold_market_data.get_1h_bars(cfg.yahoo_symbol)
    except MarketDataError as exc:
        error = {"type": "market_data_error", "detail": str(exc)}
        log_event(error)
        return {"events": [], "errors": [error], "mirror_events": []}

    events = process_gold_tick(cfg, state, bars_5m, bars_1h, now)
    for event in events:
        log_event({"strategy": "gold_orb", **event})

    mirror_events: list[dict] = []
    if mirror is not None:
        mirror_events = mirror.handle_events(events)
        mirror_events.extend(mirror.reconcile())
        for event in mirror_events:
            log_event({"strategy": "gold_orb_mirror", **event})

    return {"events": events, "errors": [], "mirror_events": mirror_events, "now_et": now.isoformat()}
