"""Virtual portfolio: cash, open positions, trade history, P&L."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from .models import Position, Trade


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Portfolio:
    starting_cash: float
    cash: float
    positions: dict[str, Position] = field(default_factory=dict)  # ticker -> Position
    trade_history: list[Trade] = field(default_factory=list)

    # Daily guardrail bookkeeping, reset each new UTC day (see ensure_daily_reset).
    trades_opened_today: int = 0
    day_start_equity: float = 0.0
    last_reset_date: str = ""  # "YYYY-MM-DD"

    @staticmethod
    def new(starting_cash: float) -> "Portfolio":
        p = Portfolio(starting_cash=starting_cash, cash=starting_cash)
        p.day_start_equity = starting_cash
        p.last_reset_date = utcnow().strftime("%Y-%m-%d")
        return p

    def get_equity(self, current_prices: dict[str, float]) -> float:
        market_value = 0.0
        for ticker, pos in self.positions.items():
            price = current_prices.get(ticker, pos.entry_price)
            market_value += pos.quantity * price
        return self.cash + market_value

    def ensure_daily_reset(self, current_prices: dict[str, float], now: datetime | None = None) -> bool:
        """Rolls daily counters over if the UTC date has changed. Returns True if it reset."""
        now = now or utcnow()
        today = now.strftime("%Y-%m-%d")
        if today == self.last_reset_date:
            return False
        self.last_reset_date = today
        self.trades_opened_today = 0
        self.day_start_equity = self.get_equity(current_prices)
        return True

    def daily_loss_pct(self, current_prices: dict[str, float]) -> float:
        """Fraction lost today relative to day_start_equity (positive = loss)."""
        if self.day_start_equity <= 0:
            return 0.0
        current_equity = self.get_equity(current_prices)
        return (self.day_start_equity - current_equity) / self.day_start_equity

    def open_position(
        self, ticker: str, fill_price: float, dollar_amount: float, stop_price: float, entry_time: datetime
    ) -> Position:
        if ticker in self.positions:
            raise ValueError(f"Position already open for {ticker!r}")
        if dollar_amount > self.cash:
            raise ValueError(
                f"Insufficient cash: need {dollar_amount:.2f}, have {self.cash:.2f}"
            )
        quantity = dollar_amount / fill_price
        position = Position(
            ticker=ticker,
            entry_price=fill_price,
            entry_time=entry_time,
            quantity=quantity,
            dollar_amount=dollar_amount,
            stop_price=stop_price,
        )
        self.cash -= dollar_amount
        self.positions[ticker] = position
        self.trades_opened_today += 1
        return position

    def close_position(
        self, ticker: str, exit_price: float, exit_time: datetime, exit_reason: str, targets_hit_count: int
    ) -> Trade:
        position = self.positions.pop(ticker)
        proceeds = position.quantity * exit_price
        pnl = proceeds - position.dollar_amount
        pnl_pct = pnl / position.dollar_amount if position.dollar_amount else 0.0
        self.cash += proceeds
        trade = Trade(
            ticker=ticker,
            entry_price=position.entry_price,
            entry_time=position.entry_time,
            exit_price=exit_price,
            exit_time=exit_time,
            quantity=position.quantity,
            dollar_amount=position.dollar_amount,
            pnl=pnl,
            pnl_pct=pnl_pct,
            exit_reason=exit_reason,
            targets_hit_count=targets_hit_count,
        )
        self.trade_history.append(trade)
        return trade

    def to_dict(self) -> dict:
        return {
            "starting_cash": self.starting_cash,
            "cash": self.cash,
            "positions": {t: p.to_dict() for t, p in self.positions.items()},
            "trade_history": [t.to_dict() for t in self.trade_history],
            "trades_opened_today": self.trades_opened_today,
            "day_start_equity": self.day_start_equity,
            "last_reset_date": self.last_reset_date,
        }

    @staticmethod
    def from_dict(d: dict) -> "Portfolio":
        return Portfolio(
            starting_cash=d["starting_cash"],
            cash=d["cash"],
            positions={t: Position.from_dict(p) for t, p in d.get("positions", {}).items()},
            trade_history=[Trade.from_dict(t) for t in d.get("trade_history", [])],
            trades_opened_today=d.get("trades_opened_today", 0),
            day_start_equity=d.get("day_start_equity", d["starting_cash"]),
            last_reset_date=d.get("last_reset_date", ""),
        )
