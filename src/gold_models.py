"""Data models for the gold Asian-session ORB strategy (separate from the
equity Signal/Position/Trade models in models.py — contracts and long/short
don't fit those dollar-denominated, long-only dataclasses)."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(s) if s else None


@dataclass
class Bar:
    timestamp: datetime  # tz-aware, America/New_York
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass
class GoldSignal:
    """An entry decision produced by gold_orb.evaluate_entry."""

    side: str  # "long" or "short"
    entry_price: float
    stop_price: float
    targets: list[float]  # ascending distance from entry, in the favorable direction


@dataclass
class GoldPosition:
    side: str  # "long" or "short"
    entry_price: float
    entry_time: datetime
    contracts: int
    stop_price: float
    initial_risk: float  # abs(entry_price - stop_price) at open, fixed — used to size trailing locks
    targets: list[float]
    targets_hit: list[float] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["entry_time"] = _iso(self.entry_time)
        return d

    @staticmethod
    def from_dict(d: dict) -> "GoldPosition":
        d = dict(d)
        d["entry_time"] = _parse_iso(d.get("entry_time"))
        return GoldPosition(**d)


@dataclass
class GoldTrade:
    side: str
    entry_price: float
    entry_time: datetime
    exit_price: float
    exit_time: datetime
    contracts: int
    pnl: float
    exit_reason: str
    targets_hit_count: int

    def to_dict(self) -> dict:
        d = asdict(self)
        d["entry_time"] = _iso(self.entry_time)
        d["exit_time"] = _iso(self.exit_time)
        return d

    @staticmethod
    def from_dict(d: dict) -> "GoldTrade":
        d = dict(d)
        d["entry_time"] = _parse_iso(d.get("entry_time"))
        d["exit_time"] = _parse_iso(d.get("exit_time"))
        return GoldTrade(**d)
