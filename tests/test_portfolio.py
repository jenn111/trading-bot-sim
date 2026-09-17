import unittest
from datetime import datetime, timedelta, timezone

from src.portfolio import Portfolio


class TestPortfolio(unittest.TestCase):
    def test_open_position_deducts_cash_and_tracks_quantity(self):
        p = Portfolio.new(10000)
        pos = p.open_position("AAPL", fill_price=100.0, dollar_amount=2500.0, stop_price=97.0, entry_time=datetime.now(timezone.utc))
        self.assertEqual(pos.quantity, 25.0)
        self.assertEqual(p.cash, 7500.0)
        self.assertEqual(p.trades_opened_today, 1)
        self.assertIn("AAPL", p.positions)

    def test_open_position_rejects_insufficient_cash(self):
        p = Portfolio.new(1000)
        with self.assertRaises(ValueError):
            p.open_position("AAPL", fill_price=100.0, dollar_amount=2500.0, stop_price=97.0, entry_time=datetime.now(timezone.utc))

    def test_open_position_rejects_duplicate_ticker(self):
        p = Portfolio.new(10000)
        now = datetime.now(timezone.utc)
        p.open_position("AAPL", fill_price=100.0, dollar_amount=2500.0, stop_price=97.0, entry_time=now)
        with self.assertRaises(ValueError):
            p.open_position("AAPL", fill_price=110.0, dollar_amount=2500.0, stop_price=100.0, entry_time=now)

    def test_close_position_computes_pnl_and_restores_cash(self):
        p = Portfolio.new(10000)
        now = datetime.now(timezone.utc)
        p.open_position("AAPL", fill_price=100.0, dollar_amount=2500.0, stop_price=97.0, entry_time=now)
        trade = p.close_position("AAPL", exit_price=110.0, exit_time=now, exit_reason="stop_hit", targets_hit_count=1)
        self.assertAlmostEqual(trade.pnl, 250.0)
        self.assertAlmostEqual(trade.pnl_pct, 0.10)
        self.assertAlmostEqual(p.cash, 10250.0)
        self.assertNotIn("AAPL", p.positions)
        self.assertEqual(len(p.trade_history), 1)

    def test_get_equity_includes_open_position_market_value(self):
        p = Portfolio.new(10000)
        now = datetime.now(timezone.utc)
        p.open_position("AAPL", fill_price=100.0, dollar_amount=2500.0, stop_price=97.0, entry_time=now)
        equity = p.get_equity({"AAPL": 120.0})
        # cash 7500 + 25 shares * 120 = 7500 + 3000 = 10500
        self.assertAlmostEqual(equity, 10500.0)

    def test_daily_reset_rolls_over_on_new_day(self):
        p = Portfolio.new(10000)
        p.trades_opened_today = 3
        yesterday = datetime.now(timezone.utc) - timedelta(days=1)
        p.last_reset_date = yesterday.strftime("%Y-%m-%d")
        reset = p.ensure_daily_reset({}, now=datetime.now(timezone.utc))
        self.assertTrue(reset)
        self.assertEqual(p.trades_opened_today, 0)

    def test_daily_reset_noop_on_same_day(self):
        p = Portfolio.new(10000)
        p.trades_opened_today = 2
        now = datetime.now(timezone.utc)
        p.last_reset_date = now.strftime("%Y-%m-%d")
        reset = p.ensure_daily_reset({}, now=now)
        self.assertFalse(reset)
        self.assertEqual(p.trades_opened_today, 2)

    def test_round_trip_serialization(self):
        p = Portfolio.new(10000)
        now = datetime.now(timezone.utc)
        p.open_position("AAPL", fill_price=100.0, dollar_amount=2500.0, stop_price=97.0, entry_time=now)
        p.close_position("AAPL", exit_price=105.0, exit_time=now, exit_reason="stop_hit", targets_hit_count=0)
        d = p.to_dict()
        p2 = Portfolio.from_dict(d)
        self.assertEqual(p2.cash, p.cash)
        self.assertEqual(len(p2.trade_history), 1)


if __name__ == "__main__":
    unittest.main()
