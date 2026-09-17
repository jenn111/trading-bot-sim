import unittest
from datetime import datetime, timezone

from src.config import Config
from src.models import Signal
from src.portfolio import Portfolio
from src.webull_client import OrderLeg, WebullApiError
from src.webull_mirror import WebullMirror


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
        webull_mirror_enabled=True,
    )
    defaults.update(overrides)
    return Config(**defaults)


class FakeWebullClient:
    """No network — records calls and returns scripted responses."""

    def __init__(self, account_id="ACC1"):
        self.account_id = account_id
        self.place_calls = []
        self.replace_calls = []
        self.cancel_calls = []
        self.order_details: dict = {}
        self.raise_on_place: Exception | None = None
        self._next_id = 0

    def _new_id(self) -> str:
        self._next_id += 1
        return f"order-{self._next_id}"

    def place_entry_with_protective_stop(
        self, symbol, quantity, entry_stop_price, protective_stop_price, client_combo_order_id
    ):
        self.place_calls.append(
            dict(
                symbol=symbol,
                quantity=quantity,
                entry_stop_price=entry_stop_price,
                protective_stop_price=protective_stop_price,
                client_combo_order_id=client_combo_order_id,
            )
        )
        if self.raise_on_place:
            raise self.raise_on_place
        master = OrderLeg(client_order_id=self._new_id(), side="BUY", stop_price=entry_stop_price)
        stop = OrderLeg(client_order_id=self._new_id(), side="SELL", stop_price=protective_stop_price)
        return master, stop

    def replace_stop_price(self, client_order_id, new_stop_price):
        self.replace_calls.append((client_order_id, new_stop_price))

    def cancel_order(self, client_order_id):
        self.cancel_calls.append(client_order_id)

    def get_order_detail(self, client_order_id):
        result = self.order_details.get(client_order_id, {})
        if isinstance(result, Exception):
            raise result
        return result


def make_signal(ticker="SUNE", entry_trigger=3.38, targets=None) -> Signal:
    return Signal(
        ticker=ticker,
        reference_price=3.05,
        entry_trigger=entry_trigger,
        profit_targets=targets or [3.59, 4.00, 4.49],
    )


class TestMirrorEntry(unittest.TestCase):
    def setUp(self):
        self.config = make_config()
        self.client = FakeWebullClient()
        self.mirror = WebullMirror(self.client, self.config, state={})
        self.portfolio = Portfolio.new(500)
        self.now = datetime.now(timezone.utc)

    def test_entry_uses_signal_trigger_not_fill_price_for_webull_stop(self):
        # Our engine filled at 4.51 (a gap past the 3.38 breakout level), but Webull's
        # own buy-stop should trigger at the ORIGINAL breakout level, not our fill price.
        self.portfolio.open_position("SUNE", fill_price=4.51, dollar_amount=125.0, stop_price=4.3747, entry_time=self.now)
        signal = make_signal(entry_trigger=3.38)

        events = self.mirror.handle_events(
            [{"type": "entry", "ticker": "SUNE"}], self.portfolio, [signal]
        )

        self.assertEqual(len(self.client.place_calls), 1)
        call = self.client.place_calls[0]
        self.assertEqual(call["entry_stop_price"], 3.38)
        self.assertAlmostEqual(call["quantity"], self.portfolio.positions["SUNE"].quantity)
        self.assertAlmostEqual(call["protective_stop_price"], 4.3747)

        self.assertEqual(events[0]["type"], "mirror_entry_placed")
        mirror_state = self.mirror.state["SUNE"]
        self.assertEqual(mirror_state["mirror_status"], "pending_entry")
        self.assertEqual(mirror_state["entry_stop_trigger"], 3.38)

    def test_entry_with_no_local_position_reports_error_and_writes_no_state(self):
        signal = make_signal()
        events = self.mirror.handle_events([{"type": "entry", "ticker": "SUNE"}], self.portfolio, [signal])
        self.assertEqual(events[0]["type"], "mirror_error")
        self.assertNotIn("SUNE", self.mirror.state)
        self.assertEqual(self.client.place_calls, [])

    def test_entry_api_error_reports_error_and_writes_no_state(self):
        self.portfolio.open_position("SUNE", fill_price=3.40, dollar_amount=125.0, stop_price=3.298, entry_time=self.now)
        self.client.raise_on_place = WebullApiError("sandbox rejected order")
        signal = make_signal()

        events = self.mirror.handle_events([{"type": "entry", "ticker": "SUNE"}], self.portfolio, [signal])

        self.assertEqual(events[0]["type"], "mirror_error")
        self.assertIn("sandbox rejected order", events[0]["detail"])
        self.assertNotIn("SUNE", self.mirror.state)


class TestMirrorTargetHitAndExit(unittest.TestCase):
    def setUp(self):
        self.config = make_config()
        self.client = FakeWebullClient()
        self.mirror = WebullMirror(self.client, self.config, state={})
        self.portfolio = Portfolio.new(500)
        self.now = datetime.now(timezone.utc)
        self.mirror.state["SUNE"] = {
            "account_id": "ACC1",
            "master_client_order_id": "master-1",
            "stop_client_order_id": "stop-1",
            "mirror_status": "open",
            "quantity": 27.0,
            "entry_stop_trigger": 3.38,
            "current_stop_price": 3.298,
            "entry_fill_price": 3.40,
            "trued_up": False,
            "exit_price": None,
            "exit_time": None,
            "created_at": self.now.isoformat(),
            "last_checked_at": None,
            "last_error": None,
        }

    def test_target_hit_replaces_stop_on_webull(self):
        events = self.mirror.handle_events(
            [{"type": "target_hit", "ticker": "SUNE", "target": 3.59, "new_stop": 3.5541}],
            self.portfolio,
            [],
        )
        self.assertEqual(self.client.replace_calls, [("stop-1", 3.5541)])
        self.assertEqual(self.mirror.state["SUNE"]["current_stop_price"], 3.5541)
        self.assertEqual(events[0]["type"], "mirror_stop_replaced")

    def test_target_hit_for_untracked_ticker_is_ignored(self):
        events = self.mirror.handle_events(
            [{"type": "target_hit", "ticker": "UNKNOWN", "target": 1.0, "new_stop": 1.0}],
            self.portfolio,
            [],
        )
        self.assertEqual(events, [])
        self.assertEqual(self.client.replace_calls, [])

    def test_exit_does_not_touch_webull_orders(self):
        events = self.mirror.handle_events(
            [{"type": "exit", "ticker": "SUNE", "price": 3.30, "pnl": -2.7, "pnl_pct": -0.021}],
            self.portfolio,
            [],
        )
        self.assertEqual(self.client.cancel_calls, [])
        self.assertEqual(self.client.replace_calls, [])
        self.assertEqual(events[0]["type"], "local_exit_noted")
        # our exit doesn't change the mirror's own tracked status
        self.assertEqual(self.mirror.state["SUNE"]["mirror_status"], "open")


class TestReconcile(unittest.TestCase):
    def setUp(self):
        self.config = make_config(initial_stop_pct=0.03)
        self.client = FakeWebullClient()
        self.mirror = WebullMirror(self.client, self.config, state={})

    def _pending_mirror(self, current_stop_price=3.2786):
        return {
            "account_id": "ACC1",
            "master_client_order_id": "master-1",
            "stop_client_order_id": "stop-1",
            "mirror_status": "pending_entry",
            "quantity": 27.0,
            "entry_stop_trigger": 3.38,
            "current_stop_price": current_stop_price,
            "entry_fill_price": None,
            "trued_up": False,
            "exit_price": None,
            "exit_time": None,
            "created_at": "2026-09-15T00:00:00+00:00",
            "last_checked_at": None,
            "last_error": None,
        }

    def test_reconcile_still_pending_makes_no_changes(self):
        self.mirror.state["SUNE"] = self._pending_mirror()
        self.client.order_details["master-1"] = {"orders": [{"status": "SUBMITTED"}]}

        events = self.mirror.reconcile()

        self.assertEqual(events, [])
        self.assertEqual(self.mirror.state["SUNE"]["mirror_status"], "pending_entry")

    def test_reconcile_fill_trues_up_stop_upward(self):
        # Estimated stop (3.38 * 0.97) is below what the actual fill implies.
        self.mirror.state["SUNE"] = self._pending_mirror(current_stop_price=3.38 * 0.97)
        self.client.order_details["master-1"] = {
            "orders": [{"status": "FILLED", "avg_filled_price": "4.55"}]
        }

        events = self.mirror.reconcile()

        mirror = self.mirror.state["SUNE"]
        self.assertEqual(mirror["mirror_status"], "open")
        self.assertEqual(mirror["entry_fill_price"], 4.55)
        expected_stop = 4.55 * 0.97
        self.assertAlmostEqual(mirror["current_stop_price"], expected_stop)
        self.assertTrue(mirror["trued_up"])
        self.assertEqual(self.client.replace_calls, [("stop-1", expected_stop)])
        self.assertEqual(events[0]["type"], "mirror_entry_filled")

    def test_reconcile_never_lowers_a_stop_already_trailed_past_targets(self):
        # Simulates the gap-through-everything case: our engine already trailed
        # the stop up via target hits before Webull's fill was confirmed.
        already_trailed_stop = 4.49 * 0.99  # far above a plain 3%-below-fill stop
        self.mirror.state["SUNE"] = self._pending_mirror(current_stop_price=already_trailed_stop)
        self.client.order_details["master-1"] = {
            "orders": [{"status": "FILLED", "avg_filled_price": "4.51"}]
        }

        self.mirror.reconcile()

        mirror = self.mirror.state["SUNE"]
        self.assertEqual(mirror["mirror_status"], "open")
        self.assertAlmostEqual(mirror["current_stop_price"], already_trailed_stop)
        self.assertEqual(self.client.replace_calls, [])  # no replace call — nothing needed trueing up
        self.assertFalse(mirror["trued_up"])

    def test_reconcile_master_cancelled_marks_mirror_as_error(self):
        self.mirror.state["SUNE"] = self._pending_mirror()
        self.client.order_details["master-1"] = {"orders": [{"status": "CANCELLED"}]}

        events = self.mirror.reconcile()

        self.assertEqual(self.mirror.state["SUNE"]["mirror_status"], "error")
        self.assertEqual(events[0]["type"], "mirror_entry_failed")

    def test_reconcile_exit_fill_closes_mirror_position(self):
        self.mirror.state["SUNE"] = {
            **self._pending_mirror(),
            "mirror_status": "open",
            "entry_fill_price": 4.55,
        }
        self.client.order_details["stop-1"] = {
            "orders": [{"status": "FILLED", "avg_filled_price": "4.35"}]
        }

        events = self.mirror.reconcile()

        mirror = self.mirror.state["SUNE"]
        self.assertEqual(mirror["mirror_status"], "closed")
        self.assertEqual(mirror["exit_price"], 4.35)
        self.assertIsNotNone(mirror["exit_time"])
        self.assertEqual(events[0]["type"], "mirror_exit_filled")

    def test_reconcile_unrecognized_response_shape_fails_soft(self):
        self.mirror.state["SUNE"] = self._pending_mirror()
        self.client.order_details["master-1"] = {"something_unexpected": True}

        events = self.mirror.reconcile()

        self.assertEqual(events, [])
        self.assertEqual(self.mirror.state["SUNE"]["mirror_status"], "pending_entry")

    def test_reconcile_api_error_fails_soft_and_records_last_error(self):
        self.mirror.state["SUNE"] = self._pending_mirror()
        self.client.order_details["master-1"] = WebullApiError("sandbox timeout")

        events = self.mirror.reconcile()

        self.assertEqual(events, [])
        self.assertEqual(self.mirror.state["SUNE"]["mirror_status"], "pending_entry")
        self.assertIn("sandbox timeout", self.mirror.state["SUNE"]["last_error"])


if __name__ == "__main__":
    unittest.main()
