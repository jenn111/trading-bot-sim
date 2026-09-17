"""Append-only poll/event log for debugging exactly when/why something triggered."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from .config import DATA_DIR, POLL_LOG_PATH


def log_event(record: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    record = {"logged_at": datetime.now(timezone.utc).isoformat(), **record}
    with open(POLL_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
