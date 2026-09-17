"""Data models for signals, positions, and trades."""
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
class Signal:
    """A manually-entered (later: Discord-sourced) trade signal.

    entry_trigger is the single price that actually fires an entry: the top
    of the entry range if a range was given, otherwise the entry level itself.
    major_breakout_level and dip_levels are reference-only and not used by
    the strategy logic in this version.
    """

    ticker: str
    reference_price: float
    entry_trigger: float
    profit_targets: list[float]  # ascending order
    entry_low: Optional[float] = None  # None if entry was a single level, not a range
    major_breakout_level: Optional[float] = None
    dip_levels: list[float] = field(default_factory=list)

    status: str = "pending"  # pending -> open -> closed
    last_checked_price: Optional[float] = None
    last_checked_at: Optional[datetime] = None
    targets_hit: list[float] = field(default_factory=list)
    created_at: datetime = field(default_factory=utcnow)
    cooldown_until: Optional[datetime] = None  # set after this ticker's position closes

    def to_dict(self) -> dict:
        d = asdict(self)
        d["last_checked_at"] = _iso(self.last_checked_at)
        d["created_at"] = _iso(self.created_at)
        d["cooldown_until"] = _iso(self.cooldown_until)
        return d

    @staticmethod
    def from_dict(d: dict) -> "Signal":
        d = dict(d)
        d["last_checked_at"] = _parse_iso(d.get("last_checked_at"))
        d["created_at"] = _parse_iso(d.get("created_at")) or utcnow()
        d["cooldown_until"] = _parse_iso(d.get("cooldown_until"))
        return Signal(**d)


@dataclass
class Position:
    ticker: str
    entry_price: float
    entry_time: datetime
    quantity: float
    dollar_amount: float
    stop_price: float

    def to_dict(self) -> dict:
        d = asdict(self)
        d["entry_time"] = _iso(self.entry_time)
        return d

    @staticmethod
    def from_dict(d: dict) -> "Position":
        d = dict(d)
        d["entry_time"] = _parse_iso(d.get("entry_time"))
        return Position(**d)


@dataclass
class Trade:
    ticker: str
    entry_price: float
    entry_time: datetime
    exit_price: float
    exit_time: datetime
    quantity: float
    dollar_amount: float
    pnl: float
    pnl_pct: float
    exit_reason: str
    targets_hit_count: int

    def to_dict(self) -> dict:
        d = asdict(self)
        d["entry_time"] = _iso(self.entry_time)
        d["exit_time"] = _iso(self.exit_time)
        return d

    @staticmethod
    def from_dict(d: dict) -> "Trade":
        d = dict(d)
        d["entry_time"] = _parse_iso(d.get("entry_time"))
        d["exit_time"] = _parse_iso(d.get("exit_time"))
        return Trade(**d)
