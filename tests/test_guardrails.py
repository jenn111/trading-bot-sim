import unittest
from datetime import datetime, timedelta, timezone

from src.config import Config
from src.guardrails import check_entry_allowed
from src.models import Signal
from src.portfolio import Portfolio


def make_config(**overrides) -> Config:
    defaults = dict(
        starting_balance=10000,
        position_size_pct=0.25,
        max_position_pct=0.25,
        max_concurrent_positions=2,
        daily_loss_cap_pct=0.07,
        max_trades_per_day=4,
        cooldown_minutes=45,
        initial_stop_pct=0.03,
        trailing_stop_pct=0.01,
        poll_interval_minutes=5,
    )
    defaults.update(overrides)
    return Config(**defaults)


def make_signal(ticker="AAPL", **overrides) -> Signal:
    defaults = dict(
        ticker=ticker,
        reference_price=100.0,
        entry_trigger=105.0,
        profit_targets=[110.0, 115.0],
    )
    defaults.update(overrides)
    return Signal(**defaults)


class TestGuardrails(unittest.TestCase):
    def test_allows_entry_when_all_clear(self):
        config = make_config()
        portfolio = Portfolio.new(10000)
        signal = make_signal()
        violations = check_entry_allowed(config, portfolio, signal, {})
        self.assertEqual(violations, [])

    def test_blocks_on_max_concurrent_positions(self):
        config = make_config(max_concurrent_positions=1)
        portfolio = Portfolio.new(10000)
        portfolio.open_position("MSFT", 100.0, 2500.0, 97.0, datetime.now(timezone.utc))
        signal = make_signal(ticker="AAPL")
        violations = check_entry_allowed(config, portfolio, signal, {"MSFT": 100.0})
        self.assertTrue(any("concurrent positions" in v for v in violations))

    def test_blocks_on_max_trades_per_day(self):
        config = make_config(max_trades_per_day=1)
        portfolio = Portfolio.new(10000)
        portfolio.trades_opened_today = 1
        signal = make_signal()
        violations = check_entry_allowed(config, portfolio, signal, {})
        self.assertTrue(any("max trades per day" in v for v in violations))

    def test_blocks_on_daily_loss_cap(self):
        config = make_config(daily_loss_cap_pct=0.05)
        portfolio = Portfolio.new(10000)
        portfolio.day_start_equity = 10000
        portfolio.cash = 9000  # 10% down from day start
        signal = make_signal()
        violations = check_entry_allowed(config, portfolio, signal, {})
        self.assertTrue(any("daily loss cap" in v for v in violations))

    def test_blocks_on_active_cooldown(self):
        config = make_config()
        portfolio = Portfolio.new(10000)
        now = datetime.now(timezone.utc)
        signal = make_signal(cooldown_until=now + timedelta(minutes=20))
        violations = check_entry_allowed(config, portfolio, signal, {}, now=now)
        self.assertTrue(any("cooldown" in v for v in violations))

    def test_allows_entry_after_cooldown_expires(self):
        config = make_config()
        portfolio = Portfolio.new(10000)
        now = datetime.now(timezone.utc)
        signal = make_signal(cooldown_until=now - timedelta(minutes=1))
        violations = check_entry_allowed(config, portfolio, signal, {}, now=now)
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
