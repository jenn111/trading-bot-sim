import unittest
from datetime import datetime, timezone

from src.config import Config
from src.models import Signal
from src.portfolio import Portfolio
from src.strategy import crossed_down, crossed_up, process_signal


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


class TestCrossingHelpers(unittest.TestCase):
    def test_crossed_up_from_below(self):
        self.assertTrue(crossed_up(3.30, 3.40, 3.38))

    def test_crossed_up_not_yet(self):
        self.assertFalse(crossed_up(3.30, 3.35, 3.38))

    def test_crossed_up_no_prior_price_already_through(self):
        self.assertTrue(crossed_up(None, 3.40, 3.38))

    def test_crossed_up_no_prior_price_below(self):
        self.assertFalse(crossed_up(None, 3.20, 3.38))

    def test_crossed_down_from_above(self):
        self.assertTrue(crossed_down(3.50, 3.40, 3.45))

    def test_crossed_down_not_yet(self):
        self.assertFalse(crossed_down(3.50, 3.48, 3.45))


class TestProcessSignal(unittest.TestCase):
    def setUp(self):
        self.config = make_config()
        self.portfolio = Portfolio.new(10000)
        self.now = datetime.now(timezone.utc)

    def test_entry_triggers_on_crossing_breakout(self):
        signal = Signal(
            ticker="SUNE", reference_price=3.10, entry_trigger=3.38,
            profit_targets=[3.75, 4.10, 4.50], entry_low=3.34, major_breakout_level=3.59,
        )
        signal.last_checked_price = 3.30
        events = process_signal(self.config, self.portfolio, signal, 3.40, {"SUNE": 3.40}, self.now)

        self.assertEqual(signal.status, "open")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "entry")
        pos = self.portfolio.positions["SUNE"]
        self.assertAlmostEqual(pos.entry_price, 3.40)
        self.assertAlmostEqual(pos.dollar_amount, 2500.0)  # 25% of 10000
        self.assertAlmostEqual(pos.stop_price, 3.40 * 0.97)

    def test_entry_gap_through_targets_hits_them_and_trails_stop_immediately(self):
        # Regression test: a signal that gaps up past entry AND past one or more
        # profit targets in a single poll must have those targets marked hit and
        # the stop trailed immediately — not left at the initial 3% stop forever,
        # since later polls will never see a fresh crossing of an already-passed target.
        signal = Signal(
            ticker="SUNE", reference_price=3.05, entry_trigger=3.38,
            profit_targets=[3.59, 4.00, 4.49], entry_low=3.34, major_breakout_level=3.59,
        )
        signal.last_checked_price = None  # first-ever poll for this signal

        events = process_signal(self.config, self.portfolio, signal, 4.51, {"SUNE": 4.51}, self.now)

        event_types = [e["type"] for e in events]
        self.assertEqual(event_types, ["entry", "target_hit", "target_hit", "target_hit"])
        self.assertEqual(signal.status, "open")
        self.assertEqual(signal.targets_hit, [3.59, 4.00, 4.49])
        expected_stop = 4.49 * 0.99
        self.assertAlmostEqual(self.portfolio.positions["SUNE"].stop_price, expected_stop)

    def test_no_entry_when_price_below_trigger(self):
        signal = Signal(ticker="SUNE", reference_price=3.10, entry_trigger=3.38, profit_targets=[3.75])
        signal.last_checked_price = 3.20
        events = process_signal(self.config, self.portfolio, signal, 3.25, {"SUNE": 3.25}, self.now)
        self.assertEqual(signal.status, "pending")
        self.assertEqual(events, [])

    def test_entry_blocked_reported_when_guardrail_fails(self):
        self.config = make_config(max_trades_per_day=0)
        signal = Signal(ticker="SUNE", reference_price=3.10, entry_trigger=3.38, profit_targets=[3.75])
        signal.last_checked_price = 3.30
        events = process_signal(self.config, self.portfolio, signal, 3.40, {"SUNE": 3.40}, self.now)
        self.assertEqual(signal.status, "pending")
        self.assertEqual(events[0]["type"], "entry_blocked")
        self.assertNotIn("SUNE", self.portfolio.positions)

    def test_target_hit_moves_stop_up(self):
        signal = Signal(ticker="SUNE", reference_price=3.10, entry_trigger=3.38, profit_targets=[3.75, 4.10])
        self.portfolio.open_position("SUNE", fill_price=3.40, dollar_amount=2500.0, stop_price=3.30, entry_time=self.now)
        signal.status = "open"
        signal.last_checked_price = 3.70

        events = process_signal(self.config, self.portfolio, signal, 3.80, {"SUNE": 3.80}, self.now)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "target_hit")
        expected_stop = 3.75 * 0.99
        self.assertAlmostEqual(self.portfolio.positions["SUNE"].stop_price, expected_stop)
        self.assertIn(3.75, signal.targets_hit)

    def test_stop_never_moves_down(self):
        signal = Signal(ticker="SUNE", reference_price=3.10, entry_trigger=3.38, profit_targets=[3.75])
        # Stop is already above what a 1%-below-target trail would compute.
        self.portfolio.open_position("SUNE", fill_price=3.40, dollar_amount=2500.0, stop_price=3.80, entry_time=self.now)
        signal.status = "open"
        signal.last_checked_price = 3.70

        process_signal(self.config, self.portfolio, signal, 3.80, {"SUNE": 3.80}, self.now)

        self.assertAlmostEqual(self.portfolio.positions["SUNE"].stop_price, 3.80)

    def test_exit_on_stop_hit_closes_position(self):
        signal = Signal(ticker="SUNE", reference_price=3.10, entry_trigger=3.38, profit_targets=[3.75])
        self.portfolio.open_position("SUNE", fill_price=3.40, dollar_amount=2500.0, stop_price=3.30, entry_time=self.now)
        signal.status = "open"
        signal.last_checked_price = 3.35

        events = process_signal(self.config, self.portfolio, signal, 3.25, {"SUNE": 3.25}, self.now)

        self.assertEqual(signal.status, "closed")
        self.assertEqual(events[0]["type"], "exit")
        self.assertNotIn("SUNE", self.portfolio.positions)
        self.assertEqual(len(self.portfolio.trade_history), 1)
        self.assertIsNotNone(signal.cooldown_until)

    def test_last_checked_price_updates_every_tick(self):
        signal = Signal(ticker="SUNE", reference_price=3.10, entry_trigger=3.38, profit_targets=[3.75])
        signal.last_checked_price = 3.20
        process_signal(self.config, self.portfolio, signal, 3.25, {"SUNE": 3.25}, self.now)
        self.assertEqual(signal.last_checked_price, 3.25)
        self.assertEqual(signal.last_checked_at, self.now)


if __name__ == "__main__":
    unittest.main()
