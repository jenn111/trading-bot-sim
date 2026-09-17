import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from src.gold_models import Bar
from src.gold_state import GoldEngineState, process_gold_tick
from tests.test_gold_orb import _bearish_orb_scenario, _bullish_orb_scenario, _trending_1h_bars, make_gold_config

ET = ZoneInfo("America/New_York")


def bar(ts, o, h, l, c):
    return Bar(timestamp=ts, open=o, high=h, low=l, close=c, volume=100.0)


class TestProcessGoldTick(unittest.TestCase):
    def setUp(self):
        self.cfg = make_gold_config()
        self.anchor = datetime(2026, 9, 16, 18, 0, tzinfo=ET)
        self.now = self.anchor + timedelta(minutes=32)
        self.bars_1h_bullish = _trending_1h_bars([10, 11, 12, 13, 14, 16])
        self.state = GoldEngineState()

    def test_entry_fires_and_opens_position(self):
        bars_5m = _bullish_orb_scenario(self.anchor)
        events = process_gold_tick(self.cfg, self.state, bars_5m, self.bars_1h_bullish, self.now)

        self.assertEqual(events[0]["type"], "entry")
        self.assertEqual(events[0]["side"], "long")
        self.assertIsNotNone(self.state.position)
        self.assertEqual(self.state.entries_today, 1)
        self.assertEqual(self.state.session_date, self.anchor.date().isoformat())

    def test_max_entries_per_session_blocks_reentry(self):
        cfg = make_gold_config(max_entries_per_session=0)
        bars_5m = _bullish_orb_scenario(self.anchor)
        events = process_gold_tick(cfg, self.state, bars_5m, self.bars_1h_bullish, self.now)
        self.assertEqual(events, [])
        self.assertIsNone(self.state.position)

    def test_target_hit_trails_stop_up_for_long(self):
        bars_5m = _bullish_orb_scenario(self.anchor)
        process_gold_tick(self.cfg, self.state, bars_5m, self.bars_1h_bullish, self.now)
        pos = self.state.position
        target = pos.targets[0]
        stop_before = pos.stop_price

        next_bars = bars_5m + [bar(self.anchor + timedelta(minutes=35), target - 1, target + 2, target - 1, target + 1)]
        events = process_gold_tick(self.cfg, self.state, next_bars, self.bars_1h_bullish, self.now + timedelta(minutes=5))

        self.assertEqual(events[0]["type"], "target_hit")
        self.assertGreater(self.state.position.stop_price, stop_before)
        self.assertIn(target, self.state.position.targets_hit)

    def test_stop_hit_closes_long_position(self):
        bars_5m = _bullish_orb_scenario(self.anchor)
        process_gold_tick(self.cfg, self.state, bars_5m, self.bars_1h_bullish, self.now)
        stop = self.state.position.stop_price

        next_bars = bars_5m + [bar(self.anchor + timedelta(minutes=35), stop + 1, stop + 1, stop - 1, stop - 0.5)]
        events = process_gold_tick(self.cfg, self.state, next_bars, self.bars_1h_bullish, self.now + timedelta(minutes=5))

        self.assertEqual(events[-1]["type"], "exit")
        self.assertEqual(events[-1]["reason"], "stop_hit")
        self.assertIsNone(self.state.position)
        self.assertEqual(len(self.state.trade_history), 1)

    def test_session_flatten_closes_position_after_cutoff(self):
        bars_5m = _bullish_orb_scenario(self.anchor)
        process_gold_tick(self.cfg, self.state, bars_5m, self.bars_1h_bullish, self.now)
        entry_price = self.state.position.entry_price

        late_time = self.anchor + timedelta(minutes=self.cfg.session_flatten_minutes_after_open + 5)
        next_bars = bars_5m + [bar(self.anchor + timedelta(minutes=35), entry_price, entry_price + 1, entry_price - 1, entry_price + 0.5)]
        events = process_gold_tick(self.cfg, self.state, next_bars, self.bars_1h_bullish, late_time)

        self.assertEqual(events[-1]["type"], "exit")
        self.assertEqual(events[-1]["reason"], "session_flatten")
        self.assertIsNone(self.state.position)

    def test_short_entry_and_stop_hit(self):
        bars_5m = _bearish_orb_scenario(self.anchor)
        bars_1h_bearish = _trending_1h_bars([16, 14, 13, 12, 11, 10])
        events = process_gold_tick(self.cfg, self.state, bars_5m, bars_1h_bearish, self.now)
        self.assertEqual(events[0]["side"], "short")

        stop = self.state.position.stop_price
        next_bars = bars_5m + [bar(self.anchor + timedelta(minutes=35), stop - 1, stop + 1, stop - 1, stop + 0.5)]
        events = process_gold_tick(self.cfg, self.state, next_bars, bars_1h_bearish, self.now + timedelta(minutes=5))
        self.assertEqual(events[-1]["type"], "exit")
        self.assertEqual(events[-1]["reason"], "stop_hit")

    def test_new_session_resets_entry_count(self):
        bars_5m = _bullish_orb_scenario(self.anchor)
        process_gold_tick(self.cfg, self.state, bars_5m, self.bars_1h_bullish, self.now)
        self.assertEqual(self.state.entries_today, 1)

        # Close out session 1's position (stop-hit) so session 2 is free to enter.
        stop = self.state.position.stop_price
        closing_bars = bars_5m + [bar(self.anchor + timedelta(minutes=35), stop + 1, stop + 1, stop - 1, stop - 0.5)]
        process_gold_tick(self.cfg, self.state, closing_bars, self.bars_1h_bullish, self.now + timedelta(minutes=5))
        self.assertIsNone(self.state.position)

        next_anchor = self.anchor + timedelta(days=1)
        next_now = next_anchor + timedelta(minutes=32)
        bars_5m_next = _bullish_orb_scenario(next_anchor)
        process_gold_tick(self.cfg, self.state, bars_5m_next, self.bars_1h_bullish, next_now)
        self.assertEqual(self.state.session_date, next_anchor.date().isoformat())
        self.assertEqual(self.state.entries_today, 1)  # reset to 0, then incremented by this session's entry


class TestGoldEngineStateSerialization(unittest.TestCase):
    def test_round_trip_with_open_position(self):
        cfg = make_gold_config()
        anchor = datetime(2026, 9, 16, 18, 0, tzinfo=ET)
        now = anchor + timedelta(minutes=32)
        state = GoldEngineState()
        bars_5m = _bullish_orb_scenario(anchor)
        bars_1h = _trending_1h_bars([10, 11, 12, 13, 14, 16])
        process_gold_tick(cfg, state, bars_5m, bars_1h, now)

        restored = GoldEngineState.from_dict(state.to_dict())
        self.assertEqual(restored.position.side, state.position.side)
        self.assertAlmostEqual(restored.position.entry_price, state.position.entry_price)
        self.assertEqual(restored.session_date, state.session_date)
        self.assertEqual(restored.entries_today, state.entries_today)


if __name__ == "__main__":
    unittest.main()
