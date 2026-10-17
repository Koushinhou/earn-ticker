"""Per-day runtime state (overtime sessions) persisted as JSON."""

from __future__ import annotations

import contextlib
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .pay import DayRecord, PayRules, close_day, toggle_overtime
from .settings import atomic_write_json, backup_corrupt, read_json

STATE_VERSION = 1
KEEP_DAYS = 400  # enough for year-to-date totals


def _dt(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value is not None else None


def record_to_json(rec: DayRecord) -> dict[str, Any]:
    return {
        "sessions": [[_iso(s), _iso(e)] for s, e in rec.sessions],
        "auto_stop": _iso(rec.auto_stop),
        "last_seen": _iso(rec.last_seen),
    }


def record_from_json(data: Any) -> DayRecord:
    if not isinstance(data, dict):
        return DayRecord()
    sessions: list[tuple[datetime, datetime | None]] = []
    for item in data.get("sessions") or []:
        if not isinstance(item, list | tuple) or not item:
            continue
        start = _dt(item[0])
        if start is None:
            continue
        end = _dt(item[1]) if len(item) > 1 else None
        sessions.append((start, end))
    return DayRecord(tuple(sessions), _dt(data.get("auto_stop")), _dt(data.get("last_seen")))


class StateStore:
    """In-memory map of date -> DayRecord backed by a JSON file."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.records: dict[date, DayRecord] = {}
        self.corrupt = False  # last load found an unreadable file
        self.backup: Path | None = None

    # -- persistence -----------------------------------------------------

    def load(self) -> None:
        self.records = {}
        self.corrupt = False
        self.backup = None
        if self.path is None or not self.path.exists():
            return
        try:
            data = read_json(self.path)
            days = data.get("days", {}) if isinstance(data, dict) else None
            if not isinstance(days, dict):
                raise ValueError("bad state file")
        except (OSError, ValueError):
            self.corrupt = True
            self.backup = backup_corrupt(self.path)
            return
        for key, value in days.items():
            try:
                d = date.fromisoformat(key)
            except ValueError:
                continue
            self.records[d] = record_from_json(value)

    def save(self) -> None:
        if self.path is None:
            return
        data = {
            "version": STATE_VERSION,
            "days": {d.isoformat(): record_to_json(r) for d, r in sorted(self.records.items())},
        }
        with contextlib.suppress(OSError):
            atomic_write_json(self.path, data)

    # -- operations -------------------------------------------------------

    def get(self, d: date) -> DayRecord:
        return self.records.get(d, DayRecord())

    def prune(self, today: date) -> None:
        cutoff = today - timedelta(days=KEEP_DAYS)
        self.records = {d: r for d, r in self.records.items() if d >= cutoff}

    def toggle(self, rules: PayRules, now: datetime) -> DayRecord:
        d = now.date()
        rec = toggle_overtime(rules, d, self.get(d), now)
        self.records[d] = rec
        self.save()
        return rec

    def touch(self, now: datetime) -> None:
        """Record that the app is alive at ``now`` (for past-day accounting)."""
        d = now.date()
        rec = self.get(d)
        self.records[d] = DayRecord(rec.sessions, rec.auto_stop, now)

    def rollover(self, rules: PayRules, old: date, new: date, now: datetime | None = None) -> None:
        """Close ``old``; continue an open manual session on ``new`` if the app
        was alive across midnight (see ``close_day``)."""
        closed, continue_open = close_day(rules, old, self.records.get(old), now)
        self.records[old] = closed
        if continue_open and new == old + timedelta(days=1):
            midnight = datetime.combine(new, datetime.min.time())
            rec = self.get(new)
            self.records[new] = DayRecord(
                (*rec.sessions, (midnight, None)), rec.auto_stop, rec.last_seen
            )
        self.prune(new)
        self.save()
