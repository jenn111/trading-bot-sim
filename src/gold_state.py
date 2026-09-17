"""Engine state for the gold ORB strategy: at most one position at a time,
contracts-based (not dollar-based), long or short. Mirrors the shape of
strategy.py/portfolio.py for the equity engine but is intentionally a
separate, simpler state machine — contracts and short-selling don't fit the
existing Position/Portfolio dataclasses.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

from .gold_config import GoldConfig
from .gold_models import Bar, GoldPosition, GoldTrade
from .gold_orb import crossed_favorably, crossed_unfavorably, evaluate_entry, session_anchor


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class GoldEngineState:
    position: Optional[GoldPosition] = None
    trade_history: list[GoldTrade] = field(default_factory=list)
    session_date: str = ""  # ISO date of the current session's anchor
    entries_today: int = 0
    last_price: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "position": self.position.to_dict() if self.position else None,
            "trade_history": [t.to_dict() for t in self.trade_history],
            "session_date": self.session_date,
            "entries_today": self.entries_today,
            "last_price": self.last_price,
        }

    @staticmethod
    def from_dict(d: dict) -> "GoldEngineState":
        return GoldEngineState(
            position=GoldPosition.from_dict(d["position"]) if d.get("position") else None,
            trade_history=[GoldTrade.from_dict(t) for t in d.get("trade_history", [])],
            session_date=d.get("session_date", ""),
            entries_today=d.get("entries_today", 0),
            last_price=d.get("last_price"),
        )


def _close_position(state: GoldEngineState, exit_price: float, exit_time: datetime, reason: str) -> GoldTrade:
    pos = state.position
    if pos.side == "long":
        pnl_points = exit_price - pos.entry_price
    else:
        pnl_points = pos.entry_price - exit_price
    trade = GoldTrade(
        side=pos.side,
        entry_price=pos.entry_price,
        entry_time=pos.entry_time,
        exit_price=exit_price,
        exit_time=exit_time,
        contracts=pos.contracts,
        pnl=pnl_points * pos.contracts,  # caller (gold_engine) scales by point_value for $ P&L
        exit_reason=reason,
        targets_hit_count=len(pos.targets_hit),
    )
    state.trade_history.append(trade)
    state.position = None
    return trade


def process_gold_tick(
    cfg: GoldConfig,
    state: GoldEngineState,
    bars_5m: list[Bar],
    bars_1h: list[Bar],
    now_et: datetime,
) -> list[dict]:
    """Evaluates one tick against the latest closed 5-minute bar. Mutates
    `state` in place. Returns a list of event dicts for logging/printing."""
    events: list[dict] = []
    if not bars_5m:
        return events

    anchor = session_anchor(now_et, cfg.session_open_hour_et)
    session_date = anchor.date().isoformat()
    if session_date != state.session_date:
        state.session_date = session_date
        state.entries_today = 0

    current_price = bars_5m[-1].close
    last_price = state.last_price

    if state.position is None:
        if state.entries_today < cfg.max_entries_per_session:
            signal = evaluate_entry(cfg, bars_5m, bars_1h, now_et)
            if signal is not None:
                state.position = GoldPosition(
                    side=signal.side,
                    entry_price=signal.entry_price,
                    entry_time=now_et,
                    contracts=cfg.contracts_per_trade,
                    stop_price=signal.stop_price,
                    initial_risk=abs(signal.entry_price - signal.stop_price),
                    targets=signal.targets,
                )
                state.entries_today += 1
                events.append(
                    {
                        "type": "entry",
                        "side": signal.side,
                        "price": signal.entry_price,
                        "stop_price": signal.stop_price,
                        "targets": signal.targets,
                        "contracts": cfg.contracts_per_trade,
                    }
                )

    if state.position is not None:
        pos = state.position
        for target in pos.targets:
            if target in pos.targets_hit:
                continue
            if crossed_favorably(pos.side, last_price, current_price, target):
                lock = cfg.trailing_lock_r * pos.initial_risk
                candidate = target - lock if pos.side == "long" else target + lock
                pos.stop_price = max(pos.stop_price, candidate) if pos.side == "long" else min(pos.stop_price, candidate)
                pos.targets_hit.append(target)
                events.append(
                    {
                        "type": "target_hit",
                        "side": pos.side,
                        "price": current_price,
                        "target": target,
                        "new_stop": pos.stop_price,
                    }
                )

        minutes_since_open = (now_et - anchor).total_seconds() / 60
        if crossed_unfavorably(pos.side, last_price, current_price, pos.stop_price):
            trade = _close_position(state, current_price, now_et, "stop_hit")
            events.append({"type": "exit", "side": trade.side, "price": current_price, "pnl_points": trade.pnl, "reason": "stop_hit"})
        elif minutes_since_open >= cfg.session_flatten_minutes_after_open:
            trade = _close_position(state, current_price, now_et, "session_flatten")
            events.append({"type": "exit", "side": trade.side, "price": current_price, "pnl_points": trade.pnl, "reason": "session_flatten"})

    state.last_price = current_price
    return events
