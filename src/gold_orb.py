"""Pure decision logic for the gold Asian-session ORB strategy.

Codifies (with simplifications, see README) the "Peachy Investor" Asian
session gold ORB video: a box drawn at the CME's 18:00 ET session open,
a higher-timeframe trend filter standing in for the video's discretionary
"larger point of view" bias, an entry window that only opens once Tokyo/Hong
Kong volume arrives, and pullback-into-the-EMA-band entries confirmed by a
candle-structure flip back in the bias direction.

No network calls here — everything takes bars in and returns decisions out,
so this is fully unit-testable against synthetic data.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from .gold_config import GoldConfig
from .gold_models import Bar, GoldSignal


def ema(values: list[float], period: int) -> list[Optional[float]]:
    """Standard EMA, seeded with an SMA of the first `period` values.

    Returns a list the same length as `values`; entries before the seed
    point are None (not enough data yet).
    """
    if period <= 0:
        raise ValueError("period must be positive")
    out: list[Optional[float]] = [None] * len(values)
    if len(values) < period:
        return out
    k = 2.0 / (period + 1)
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def session_anchor(now_et: datetime, session_open_hour_et: int) -> datetime:
    """The most recent session-open timestamp at or before `now_et`.

    CME's gold session opens at 18:00 ET the prior calendar day relative to
    the trading date it's associated with (e.g. Sunday 18:00 ET starts
    Monday's session).
    """
    anchor = now_et.replace(hour=session_open_hour_et, minute=0, second=0, microsecond=0)
    if now_et < anchor:
        anchor -= timedelta(days=1)
    return anchor


@dataclass
class ORB:
    box15_high: float
    box15_low: float


def compute_orb(bars_5m: list[Bar], anchor: datetime, orb15_minutes: int) -> Optional[ORB]:
    """High/low of the first `orb15_minutes` of 5-minute bars after anchor.

    None if we don't yet have a bar covering the full window.
    """
    window_end = anchor + timedelta(minutes=orb15_minutes)
    window_bars = [b for b in bars_5m if anchor <= b.timestamp < window_end]
    if not window_bars:
        return None
    last_bar_end = window_bars[-1].timestamp + timedelta(minutes=5)
    if last_bar_end < window_end:
        return None  # window not fully closed yet
    return ORB(
        box15_high=max(b.high for b in window_bars),
        box15_low=min(b.low for b in window_bars),
    )


def daily_bias(bars_1h: list[Bar], ema_fast_period: int, ema_slow_period: int) -> Optional[str]:
    """"bullish" / "bearish" / None (flat — no trade), from an EMA stack on
    1-hour closes. Stands in for the video's discretionary 4H/KPL bias call.
    """
    closes = [b.close for b in bars_1h]
    fast = ema(closes, ema_fast_period)
    slow = ema(closes, ema_slow_period)
    if not closes or fast[-1] is None or slow[-1] is None:
        return None
    last_close, last_fast, last_slow = closes[-1], fast[-1], slow[-1]
    if last_close > last_fast > last_slow:
        return "bullish"
    if last_close < last_fast < last_slow:
        return "bearish"
    return None


def _orb_holds(bars_since_anchor: list[Bar], bias: str, orb: ORB) -> bool:
    """True if price hasn't closed against the bias side of the ORB box —
    i.e. the box has acted as support (bullish) or resistance (bearish)."""
    if bias == "bullish":
        return all(b.close >= orb.box15_low for b in bars_since_anchor)
    return all(b.close <= orb.box15_high for b in bars_since_anchor)


def evaluate_entry(
    cfg: GoldConfig,
    bars_5m: list[Bar],
    bars_1h: list[Bar],
    now_et: datetime,
) -> Optional[GoldSignal]:
    """Returns a GoldSignal if entry conditions are met on the most recently
    *closed* 5-minute bar, else None. Caller is responsible for session/entry
    -count gating (evaluate_entry only judges the current bar in isolation).
    """
    anchor = session_anchor(now_et, cfg.session_open_hour_et)
    minutes_since_open = (now_et - anchor).total_seconds() / 60
    if not (cfg.entry_window_start_minutes_after_open <= minutes_since_open <= cfg.entry_window_end_minutes_after_open):
        return None

    orb = compute_orb(bars_5m, anchor, cfg.orb15_minutes)
    if orb is None:
        return None

    bias = daily_bias(bars_1h, cfg.bias_ema_fast, cfg.bias_ema_slow)
    if bias is None:
        return None

    session_bars = [b for b in bars_5m if b.timestamp >= anchor]
    if len(session_bars) < max(cfg.swing_lookback_bars, cfg.ema_slow) + 2:
        return None
    if not _orb_holds(session_bars, bias, orb):
        return None

    closes = [b.close for b in session_bars]
    ema_fast_vals = ema(closes, cfg.ema_fast)
    ema_slow_vals = ema(closes, cfg.ema_slow)

    idx = len(session_bars) - 1  # most recently closed bar
    prev_idx = idx - 1
    bar, prev = session_bars[idx], session_bars[prev_idx]
    fast_now, slow_prev = ema_fast_vals[idx], ema_slow_vals[prev_idx]
    if fast_now is None or slow_prev is None:
        return None

    if bias == "bullish":
        retraced = prev.low <= slow_prev
        flipped = bar.close > bar.open and bar.close > prev.high and bar.close > fast_now
        if not (retraced and flipped):
            return None
        swing_low = min(b.low for b in session_bars[max(0, idx - cfg.swing_lookback_bars):idx])
        stop = swing_low - cfg.stop_buffer_ticks * cfg.tick_size
        entry_price = bar.close
        risk = entry_price - stop
        if risk <= 0:
            return None
        targets = [entry_price + risk * r for r in cfg.profit_target_r_multiples]
        return GoldSignal(side="long", entry_price=entry_price, stop_price=stop, targets=targets)

    else:  # bearish
        retraced = prev.high >= slow_prev
        flipped = bar.close < bar.open and bar.close < prev.low and bar.close < fast_now
        if not (retraced and flipped):
            return None
        swing_high = max(b.high for b in session_bars[max(0, idx - cfg.swing_lookback_bars):idx])
        stop = swing_high + cfg.stop_buffer_ticks * cfg.tick_size
        entry_price = bar.close
        risk = stop - entry_price
        if risk <= 0:
            return None
        targets = [entry_price - risk * r for r in cfg.profit_target_r_multiples]
        return GoldSignal(side="short", entry_price=entry_price, stop_price=stop, targets=targets)


def crossed_favorably(side: str, last_price: Optional[float], current_price: float, level: float) -> bool:
    if last_price is None:
        last_price = current_price
    if side == "long":
        return last_price < level <= current_price
    return last_price > level >= current_price


def crossed_unfavorably(side: str, last_price: Optional[float], current_price: float, level: float) -> bool:
    if last_price is None:
        last_price = current_price
    if side == "long":
        return last_price > level >= current_price
    return last_price < level <= current_price
