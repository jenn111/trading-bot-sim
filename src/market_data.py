"""Real market data fetching (Yahoo Finance via yfinance)."""
from __future__ import annotations

from datetime import datetime, timezone

try:
    import yfinance as yf
except ImportError:  # allows the rest of the package to be imported/tested without yfinance installed
    yf = None


class MarketDataError(Exception):
    pass


def get_current_price(ticker: str) -> tuple[float, datetime]:
    """Fetch the latest available price for ticker.

    Returns (price, fetched_at_utc). Raises MarketDataError on any failure
    (invalid ticker, network issue, missing data) so callers can log and
    skip that tick rather than crashing the whole run.
    """
    if yf is None:
        raise MarketDataError(
            "yfinance is not installed. Run `pip install -r requirements.txt`."
        )

    try:
        info = yf.Ticker(ticker).fast_info
        price = info["last_price"]
    except Exception as exc:  # yfinance can raise a variety of error types
        raise MarketDataError(f"Failed to fetch price for {ticker!r}: {exc}") from exc

    if price is None:
        raise MarketDataError(f"No price data returned for {ticker!r}")

    return float(price), datetime.now(timezone.utc)
