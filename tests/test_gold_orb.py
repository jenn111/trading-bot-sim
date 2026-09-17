import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from src.gold_config import GoldConfig
from src.gold_models import Bar
from src.gold_orb import (
    compute_orb,
    crossed_favorably,
    crossed_unfavorably,
    daily_bias,
    ema,
    evaluate_entry,
    session_anchor,
)

ET = ZoneInfo("America/New_York")


def make_gold_config(**overrides) -> GoldConfig:
    defaults = dict(
        yahoo_symbol="GC=F",
        webull_futures_product="MGC",
        contracts_per_trade=1,
        point_value=10.0,
        tick_size=0.1,
        session_open_hour_et=18,
        orb15_minutes=15,
        entry_window_start_minutes_after_open=0,
        entry_window_end_minutes_after_open=1000,
        session_flatten_minutes_after_open=780,
        ema_fast=2,
        ema_slow=3,
        bias_ema_fast=2,
        bias_ema_slow=3,
        swing_lookback_bars=3,
        stop_buffer_ticks=1,
        profit_target_r_multiples=[1.0, 2.0, 3.0],
        trailing_lock_r=0.2,
        max_entries_per_session=2,
        poll_interval_minutes=5,
    )
    defaults.update(overrides)
    return GoldConfig(**defaults)


def bar(ts, o, h, l, c) -> Bar:
    return Bar(timestamp=ts, open=o, high=h, low=l, close=c, volume=100.0)


class TestEma(unittest.TestCase):
    def test_ema_seeded_with_sma_then_smoothed(self):
        result = ema([1, 2, 3, 4, 5], period=3)
        self.assertEqual(result[:2], [None, None])
        self.assertAlmostEqual(result[2], 2.0)
        self.assertAlmostEqual(result[3], 3.0)
        self.assertAlmostEqual(result[4], 4.0)

    def test_ema_not_enough_data(self):
        result = ema([1, 2], period=3)
        self.assertEqual(result, [None, None])


class TestSessionAnchor(unittest.TestCase):
    def test_anchor_same_day_after_open(self):
        now = datetime(2026, 9, 16, 19, 30, tzinfo=ET)
        self.assertEqual(session_anchor(now, 18), datetime(2026, 9, 16, 18, 0, tzinfo=ET))

    def test_anchor_prior_day_before_open(self):
        now = datetime(2026, 9, 16, 10, 0, tzinfo=ET)
        self.assertEqual(session_anchor(now, 18), datetime(2026, 9, 15, 18, 0, tzinfo=ET))


class TestComputeOrb(unittest.TestCase):
    def test_orb_from_first_three_5m_bars(self):
        anchor = datetime(2026, 9, 16, 18, 0, tzinfo=ET)
        bars = [
            bar(anchor, 100, 101, 99, 100),
            bar(anchor + timedelta(minutes=5), 100, 102, 98, 100),
            bar(anchor + timedelta(minutes=10), 100, 100.5, 99.5, 100),
        ]
        orb = compute_orb(bars, anchor, 15)
        self.assertIsNotNone(orb)
        self.assertEqual(orb.box15_high, 102)
        self.assertEqual(orb.box15_low, 98)

    def test_orb_none_when_window_not_closed(self):
        anchor = datetime(2026, 9, 16, 18, 0, tzinfo=ET)
        bars = [bar(anchor, 100, 101, 99, 100), bar(anchor + timedelta(minutes=5), 100, 102, 98, 100)]
        self.assertIsNone(compute_orb(bars, anchor, 15))


class TestDailyBias(unittest.TestCase):
    def test_bullish_stack(self):
        closes = [10, 11, 12, 13, 14, 16]
        bars = [bar(None, c, c, c, c) for c in closes]
        self.assertEqual(daily_bias(bars, 2, 3), "bullish")

    def test_bearish_stack(self):
        closes = [16, 14, 13, 12, 11, 10]
        bars = [bar(None, c, c, c, c) for c in closes]
        self.assertEqual(daily_bias(bars, 2, 3), "bearish")

    def test_no_bias_when_flat(self):
        closes = [10, 10, 10, 10, 10, 10]
        bars = [bar(None, c, c, c, c) for c in closes]
        self.assertIsNone(daily_bias(bars, 2, 3))

    def test_no_bias_with_insufficient_data(self):
        bars = [bar(None, 10, 10, 10, 10)]
        self.assertIsNone(daily_bias(bars, 2, 3))


class TestCrossing(unittest.TestCase):
    def test_crossed_favorably_long(self):
        self.assertTrue(crossed_favorably("long", 100, 105, 104))
        self.assertFalse(crossed_favorably("long", 100, 103, 104))

    def test_crossed_favorably_short(self):
        self.assertTrue(crossed_favorably("short", 100, 95, 96))
        self.assertFalse(crossed_favorably("short", 100, 97, 96))

    def test_crossed_unfavorably_long(self):
        self.assertTrue(crossed_unfavorably("long", 100, 95, 96))

    def test_crossed_unfavorably_short(self):
        self.assertTrue(crossed_unfavorably("short", 100, 105, 104))


def _bullish_orb_scenario(anchor):
    """7 bars: 3 forming the ORB box (flat @100), 2 filler bars establishing
    an uptrend (@105), then a retracement bar dipping into the EMA band,
    then a confirmation bar flipping back up through the fast EMA and the
    prior bar's high."""
    return [
        bar(anchor, 100, 101, 99, 100),
        bar(anchor + timedelta(minutes=5), 100, 100.5, 99.5, 100),
        bar(anchor + timedelta(minutes=10), 100, 100.5, 99.5, 100),
        bar(anchor + timedelta(minutes=15), 105, 106, 104, 105),
        bar(anchor + timedelta(minutes=20), 105, 106, 104, 105),
        bar(anchor + timedelta(minutes=25), 106, 107, 102, 104),  # retracement
        bar(anchor + timedelta(minutes=30), 104, 109, 103, 108),  # confirmation flip
    ]


def _bearish_orb_scenario(anchor):
    return [
        bar(anchor, 100, 101, 99, 100),
        bar(anchor + timedelta(minutes=5), 100, 100.5, 99.5, 100),
        bar(anchor + timedelta(minutes=10), 100, 100.5, 99.5, 100),
        bar(anchor + timedelta(minutes=15), 95, 96, 94, 95),
        bar(anchor + timedelta(minutes=20), 95, 96, 94, 95),
        bar(anchor + timedelta(minutes=25), 94, 98, 93, 96),  # retracement up
        bar(anchor + timedelta(minutes=30), 96, 97, 91, 92),  # confirmation flip down
    ]


def _flat_1h_bars(n=6, level=100.0):
    return [bar(None, level, level, level, level) for _ in range(n)]


def _trending_1h_bars(closes):
    return [bar(None, c, c, c, c) for c in closes]


class TestEvaluateEntry(unittest.TestCase):
    def setUp(self):
        self.cfg = make_gold_config()
        self.anchor = datetime(2026, 9, 16, 18, 0, tzinfo=ET)
        self.now = self.anchor + timedelta(minutes=32)

    def test_long_entry_on_retracement_and_flip(self):
        bars_5m = _bullish_orb_scenario(self.anchor)
        bars_1h = _trending_1h_bars([10, 11, 12, 13, 14, 16])
        signal = evaluate_entry(self.cfg, bars_5m, bars_1h, self.now)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.side, "long")
        self.assertAlmostEqual(signal.entry_price, 108)
        self.assertLess(signal.stop_price, signal.entry_price)
        self.assertEqual(len(signal.targets), 3)
        self.assertTrue(all(t > signal.entry_price for t in signal.targets))

    def test_short_entry_on_retracement_and_flip(self):
        bars_5m = _bearish_orb_scenario(self.anchor)
        bars_1h = _trending_1h_bars([16, 14, 13, 12, 11, 10])
        signal = evaluate_entry(self.cfg, bars_5m, bars_1h, self.now)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.side, "short")
        self.assertAlmostEqual(signal.entry_price, 92)
        self.assertGreater(signal.stop_price, signal.entry_price)
        self.assertTrue(all(t < signal.entry_price for t in signal.targets))

    def test_no_entry_outside_entry_window(self):
        cfg = make_gold_config(entry_window_start_minutes_after_open=120, entry_window_end_minutes_after_open=360)
        bars_5m = _bullish_orb_scenario(self.anchor)
        bars_1h = _trending_1h_bars([10, 11, 12, 13, 14, 16])
        signal = evaluate_entry(cfg, bars_5m, bars_1h, self.now)  # now is only 32min after anchor
        self.assertIsNone(signal)

    def test_no_entry_without_bias(self):
        bars_5m = _bullish_orb_scenario(self.anchor)
        bars_1h = _flat_1h_bars()
        signal = evaluate_entry(self.cfg, bars_5m, bars_1h, self.now)
        self.assertIsNone(signal)

    def test_no_entry_when_orb_does_not_hold(self):
        bars_5m = _bullish_orb_scenario(self.anchor)
        # Force a close below the ORB box low, breaking the "held as support" filter.
        bars_5m[3] = bar(bars_5m[3].timestamp, 105, 106, 90, 91)
        bars_1h = _trending_1h_bars([10, 11, 12, 13, 14, 16])
        signal = evaluate_entry(self.cfg, bars_5m, bars_1h, self.now)
        self.assertIsNone(signal)


if __name__ == "__main__":
    unittest.main()
