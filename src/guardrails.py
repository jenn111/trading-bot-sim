"""Risk guardrails that gate new position entries."""
from __future__ import annotations

from datetime import datetime, timezone

from .config import Config
from .models import Signal
from .portfolio import Portfolio


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def check_entry_allowed(
    config: Config,
    portfolio: Portfolio,
    signal: Signal,
    current_prices: dict[str, float],
    now: datetime | None = None,
) -> list[str]:
    """Returns a list of guardrail violation reasons. Empty list = entry allowed."""
    now = now or utcnow()
    reasons: list[str] = []

    if signal.cooldown_until and now < signal.cooldown_until:
        minutes_left = (signal.cooldown_until - now).total_seconds() / 60
        reasons.append(
            f"cooldown active for {signal.ticker}: {minutes_left:.1f} min remaining"
        )

    if len(portfolio.positions) >= config.max_concurrent_positions:
        reasons.append(
            f"max concurrent positions reached ({config.max_concurrent_positions})"
        )

    if portfolio.trades_opened_today >= config.max_trades_per_day:
        reasons.append(f"max trades per day reached ({config.max_trades_per_day})")

    loss_pct = portfolio.daily_loss_pct(current_prices)
    if loss_pct >= config.daily_loss_cap_pct:
        reasons.append(
            f"daily loss cap hit ({loss_pct:.1%} >= {config.daily_loss_cap_pct:.1%}); "
            "new entries halted for the day"
        )

    equity = portfolio.get_equity(current_prices)
    dollar_amount = equity * config.position_size_pct
    if dollar_amount > dollar_amount_cap(config, equity):
        reasons.append(
            f"position size {dollar_amount:.2f} exceeds max_position_pct cap"
        )
    if dollar_amount > portfolio.cash:
        reasons.append(
            f"insufficient cash: need {dollar_amount:.2f}, have {portfolio.cash:.2f}"
        )

    return reasons


def dollar_amount_cap(config: Config, equity: float) -> float:
    return equity * config.max_position_pct
