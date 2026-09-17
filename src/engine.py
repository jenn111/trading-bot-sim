"""Ties market data + strategy + portfolio + guardrails together for one evaluation pass."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from .config import Config
from .logging_utils import log_event
from .market_data import MarketDataError, get_current_price
from .models import Signal
from .portfolio import Portfolio
from .strategy import process_signal
from .webull_mirror import WebullMirror


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def run_tick(
    config: Config,
    portfolio: Portfolio,
    signals: list[Signal],
    mirror: Optional[WebullMirror] = None,
) -> dict:
    """Runs one evaluation pass over all active (pending/open) signals.

    Fetches a fresh price for every active ticker first, so that equity and
    guardrail checks during this tick see all open positions marked to the
    same poll rather than a stale mix of old and new prices. Mutates
    `portfolio` and the `signals` list in place.
    """
    now = utcnow()
    active_signals = [s for s in signals if s.status in ("pending", "open")]

    current_prices: dict[str, float] = {}
    errors: list[dict] = []

    for signal in active_signals:
        try:
            price, fetched_at = get_current_price(signal.ticker)
        except MarketDataError as exc:
            error = {"type": "market_data_error", "ticker": signal.ticker, "detail": str(exc)}
            errors.append(error)
            log_event(error)
            continue
        current_prices[signal.ticker] = price
        log_event(
            {
                "type": "poll",
                "ticker": signal.ticker,
                "price": price,
                "fetched_at": fetched_at.isoformat(),
            }
        )

    portfolio.ensure_daily_reset(current_prices, now)

    events: list[dict] = []
    for signal in active_signals:
        if signal.ticker not in current_prices:
            continue  # price fetch failed this tick; skip evaluation, keep last_checked_price as-is
        signal_events = process_signal(
            config, portfolio, signal, current_prices[signal.ticker], current_prices, now
        )
        for event in signal_events:
            log_event(event)
        events.extend(signal_events)

    mirror_events: list[dict] = []
    if mirror is not None:
        mirror_events = mirror.handle_events(events, portfolio, signals)
        mirror_events.extend(mirror.reconcile())
        for event in mirror_events:
            log_event(event)

    return {
        "events": events,
        "errors": errors,
        "current_prices": current_prices,
        "mirror_events": mirror_events,
    }
