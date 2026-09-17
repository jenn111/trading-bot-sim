"""Real gold futures price bars (Yahoo Finance via yfinance), timezone-
normalized to America/New_York so gold_orb's session-anchor math is simple."""
from __future__ import annotations

from zoneinfo import ZoneInfo

from .gold_models import Bar
from .market_data import MarketDataError

try:
    import yfinance as yf
except ImportError:  # allows the rest of the package to be imported/tested without yfinance installed
    yf = None

ET = ZoneInfo("America/New_York")


def _fetch_bars(symbol: str, interval: str, period: str) -> list[Bar]:
    if yf is None:
        raise MarketDataError("yfinance is not installed. Run `pip install -r requirements.txt`.")
    try:
        hist = yf.Ticker(symbol).history(period=period, interval=interval)
    except Exception as exc:
        raise MarketDataError(f"Failed to fetch {interval} bars for {symbol!r}: {exc}") from exc

    if hist is None or hist.empty:
        raise MarketDataError(f"No {interval} bar data returned for {symbol!r}")

    bars: list[Bar] = []
    for ts, row in hist.iterrows():
        ts_et = ts.tz_convert(ET) if ts.tzinfo is not None else ts.tz_localize("UTC").tz_convert(ET)
        bars.append(
            Bar(
                timestamp=ts_et.to_pydatetime(),
                open=float(row["Open"]),
                high=float(row["High"]),
                low=float(row["Low"]),
                close=float(row["Close"]),
                volume=float(row.get("Volume", 0.0) or 0.0),
            )
        )
    return bars


def get_5m_bars(symbol: str, period: str = "5d") -> list[Bar]:
    return _fetch_bars(symbol, "5m", period)


def get_1h_bars(symbol: str, period: str = "30d") -> list[Bar]:
    return _fetch_bars(symbol, "1h", period)


def now_et():
    from datetime import datetime

    return datetime.now(ET)
