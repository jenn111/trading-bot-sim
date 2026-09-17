"""Entry / trailing-stop / exit logic, driven by polled price crossings."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from .config import Config
from .guardrails import check_entry_allowed
from .models import Signal
from .portfolio import Portfolio


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def crossed_up(last_price: Optional[float], current_price: float, level: float) -> bool:
    """True if price moved from below `level` to at/above it since the last poll.

    With no prior price to compare against (first poll of this signal), being
    already at or through the level counts as crossed rather than being missed.
    """
    if last_price is None:
        return current_price >= level
    return last_price < level <= current_price


def crossed_down(last_price: Optional[float], current_price: float, level: float) -> bool:
    """True if price moved from above `level` to at/below it since the last poll."""
    if last_price is None:
        return current_price <= level
    return last_price > level >= current_price


def process_signal(
    config: Config,
    portfolio: Portfolio,
    signal: Signal,
    current_price: float,
    current_prices: dict[str, float],
    now: datetime | None = None,
) -> list[dict]:
    """Evaluates one signal against its latest polled price.

    Mutates `portfolio` and `signal` in place as actions are taken. Returns a
    list of event dicts describing what happened this tick, for logging.
    """
    now = now or utcnow()
    last_price = signal.last_checked_price
    events: list[dict] = []

    if signal.status == "pending":
        if crossed_up(last_price, current_price, signal.entry_trigger):
            violations = check_entry_allowed(config, portfolio, signal, current_prices, now)
            if violations:
                events.append(
                    {
                        "type": "entry_blocked",
                        "ticker": signal.ticker,
                        "price": current_price,
                        "reasons": violations,
                    }
                )
            else:
                equity = portfolio.get_equity(current_prices)
                dollar_amount = equity * config.position_size_pct
                stop_price = current_price * (1 - config.initial_stop_pct)
                portfolio.open_position(
                    ticker=signal.ticker,
                    fill_price=current_price,
                    dollar_amount=dollar_amount,
                    stop_price=stop_price,
                    entry_time=now,
                )
                signal.status = "open"
                events.append(
                    {
                        "type": "entry",
                        "ticker": signal.ticker,
                        "price": current_price,
                        "dollar_amount": dollar_amount,
                        "stop_price": stop_price,
                    }
                )

    if signal.status == "open":
        position = portfolio.positions[signal.ticker]

        for target in signal.profit_targets:
            if target in signal.targets_hit:
                continue
            if crossed_up(last_price, current_price, target):
                new_stop = target * (1 - config.trailing_stop_pct)
                position.stop_price = max(position.stop_price, new_stop)
                signal.targets_hit.append(target)
                events.append(
                    {
                        "type": "target_hit",
                        "ticker": signal.ticker,
                        "price": current_price,
                        "target": target,
                        "new_stop": position.stop_price,
                    }
                )

        if crossed_down(last_price, current_price, position.stop_price):
            trade = portfolio.close_position(
                ticker=signal.ticker,
                exit_price=current_price,
                exit_time=now,
                exit_reason="stop_hit",
                targets_hit_count=len(signal.targets_hit),
            )
            signal.status = "closed"
            signal.cooldown_until = now + timedelta(minutes=config.cooldown_minutes)
            events.append(
                {
                    "type": "exit",
                    "ticker": signal.ticker,
                    "price": current_price,
                    "pnl": trade.pnl,
                    "pnl_pct": trade.pnl_pct,
                }
            )

    signal.last_checked_price = current_price
    signal.last_checked_at = now
    return events
