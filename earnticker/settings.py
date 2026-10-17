"""User settings: dataclass, JSON load/save and config-directory resolution."""

from __future__ import annotations

import json
import math
import os
import shutil
import sys
import tempfile
from dataclasses import asdict, dataclass, field, fields
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, NamedTuple

from .pay import PayRules

APP_NAME = "EarnTicker"
SETTINGS_FILE = "earnticker_settings.json"
STATE_FILE = "earnticker_state.json"
# JSON files written by other tools (e.g. Notepad) may start with a BOM
READ_ENCODING = "utf-8-sig"

MAX_ANNUAL_INCOME = 1e10
COEF_RANGE = (1.0, 10.0)
CAP_RANGE = (0.0, 12.0)


def _default_weekdays() -> list[int]:
    return [0, 1, 2, 3, 4]


@dataclass
class Settings:
    # income / rate
    annual_income: float = 6_000_000.0
    currency_symbol: str = "¥"
    days_in_year: int = 365
    rest_days: int = 125
    annual_leave_days: int = 20
    # schedule
    start_time: str = "08:45"
    end_time: str = "17:15"
    work_weekdays: list[int] = field(default_factory=_default_weekdays)
    holidays: list[str] = field(default_factory=list)
    # overtime
    auto_overtime: bool = True
    coef_weekday: float = 1.25
    coef_late_night: float = 1.5
    coef_rest_day: float = 1.35
    coef_rest_late_night: float = 1.6
    auto_overtime_cap_hours: float = 3.0
    # take-home
    take_home_enabled: bool = False
    take_home_ratio: float = 0.8
    # display
    show_month_total: bool = False
    show_year_total: bool = False
    today_decimals: int = 2
    total_decimals: int = 0
    amount_color: str = "#FFD700"
    clock_color: str = "#D0D0D0"
    font_size: int = 30
    panel_width: int = 260
    opacity: float = 0.8
    panel_x: int | None = None
    panel_y: int | None = None
    # system
    autostart: bool = False

    # -- conversion -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> Settings:
        """Build settings from parsed JSON, falling back per field on bad values."""
        result = cls()
        if not isinstance(data, dict):
            return result
        defaults = cls()
        for f in fields(cls):
            if f.name not in data:
                continue
            value = _coerce(data[f.name], getattr(defaults, f.name), f.name)
            if value is not None or f.name in ("panel_x", "panel_y"):
                setattr(result, f.name, value)
        return result.normalized()

    def normalized(self) -> Settings:
        """Clamp values to sane ranges (never raises).

        NaN / infinity fall back to the default; finite values are clamped.
        Rest days and annual leave are reduced so at least one working day
        remains.
        """
        d = _DEFAULTS
        self.annual_income = _clamp_float(
            self.annual_income, d.annual_income, 0.0, MAX_ANNUAL_INCOME
        )
        for name in ("coef_weekday", "coef_late_night", "coef_rest_day", "coef_rest_late_night"):
            setattr(self, name, _clamp_float(getattr(self, name), getattr(d, name), *COEF_RANGE))
        self.auto_overtime_cap_hours = _clamp_float(
            self.auto_overtime_cap_hours, d.auto_overtime_cap_hours, *CAP_RANGE
        )
        self.take_home_ratio = _clamp_float(self.take_home_ratio, d.take_home_ratio, 0.0, 1.0)
        self.opacity = _clamp_float(self.opacity, d.opacity, 0.0, 1.0)
        self.days_in_year = max(1, min(int(self.days_in_year), 366))
        self.rest_days = max(0, min(int(self.rest_days), self.days_in_year - 1))
        self.annual_leave_days = max(
            0, min(int(self.annual_leave_days), self.days_in_year - 1 - self.rest_days)
        )
        self.today_decimals = max(0, min(self.today_decimals, 4))
        self.total_decimals = max(0, min(self.total_decimals, 4))
        self.font_size = max(10, min(self.font_size, 120))
        self.panel_width = max(80, min(self.panel_width, 2000))
        self.work_weekdays = sorted({d for d in self.work_weekdays if 0 <= d <= 6})
        self.holidays = sorted({h for h in self.holidays if _parse_date(h) is not None})
        if parse_hhmm(self.start_time) is None:
            self.start_time = "08:45"
        if parse_hhmm(self.end_time) is None:
            self.end_time = "17:15"
        return self

    def copy(self) -> Settings:
        return Settings.from_dict(self.to_dict())

    def pay_rules(self) -> PayRules:
        return PayRules(
            annual_income=float(self.annual_income),
            days_in_year=int(self.days_in_year),
            rest_days=int(self.rest_days),
            annual_leave_days=int(self.annual_leave_days),
            start=parse_hhmm(self.start_time) or time(8, 45),
            end=parse_hhmm(self.end_time) or time(17, 15),
            work_weekdays=frozenset(self.work_weekdays),
            holidays=frozenset(d for d in map(_parse_date, self.holidays) if d is not None),
            auto_overtime=bool(self.auto_overtime),
            coef_weekday=float(self.coef_weekday),
            coef_late_night=float(self.coef_late_night),
            coef_rest_day=float(self.coef_rest_day),
            coef_rest_late_night=float(self.coef_rest_late_night),
            auto_overtime_cap_hours=float(self.auto_overtime_cap_hours),
            take_home_enabled=bool(self.take_home_enabled),
            take_home_ratio=float(self.take_home_ratio),
        )


_DEFAULTS = Settings()


def _clamp_float(value: Any, default: float, lo: float, hi: float) -> float:
    try:
        x = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if not math.isfinite(x):
        return default
    return max(lo, min(x, hi))


def _coerce(value: Any, default: Any, name: str) -> Any:
    """Coerce ``value`` to the type of ``default``; None if impossible."""
    try:
        if name in ("panel_x", "panel_y"):
            return None if value is None else int(value)
        if isinstance(default, bool):
            return value if isinstance(value, bool) else None
        if isinstance(default, int):
            if isinstance(value, bool):
                return None
            return int(value)
        if isinstance(default, float):
            if isinstance(value, bool):
                return None
            return float(value)
        if isinstance(default, str):
            return value if isinstance(value, str) else None
        if isinstance(default, list):
            if not isinstance(value, list):
                return None
            if name == "work_weekdays":
                return [int(v) for v in value]
            return [str(v) for v in value]
    except (TypeError, ValueError, OverflowError):
        return None
    return None


def parse_hhmm(text: str) -> time | None:
    try:
        hh, mm = text.strip().split(":")[:2]
        return time(int(hh), int(mm))
    except (ValueError, AttributeError):
        return None


def _parse_date(text: Any) -> date | None:
    try:
        return date.fromisoformat(str(text).strip())
    except ValueError:
        return None


def parse_holiday_file(data: Any) -> list[str]:
    """Extract ISO dates from an imported holiday JSON document.

    Accepts a list of strings, a list of ``{"date": ...}`` objects, a mapping
    of date -> name, or an object with a ``"holidays"`` key holding any of
    those.
    """
    if isinstance(data, dict) and "holidays" in data:
        data = data["holidays"]
    items: list[Any]
    if isinstance(data, dict):
        items = list(data.keys())
    elif isinstance(data, list):
        items = [x.get("date") if isinstance(x, dict) else x for x in data]
    else:
        items = []
    out = {d.isoformat() for d in map(_parse_date, items) if d is not None}
    return sorted(out)


# ---------------------------------------------------------------------------
# paths


def appdata_dir() -> Path:
    base = os.environ.get("APPDATA")
    root = Path(base) if base else Path.home() / "AppData" / "Roaming"
    return root / APP_NAME


def is_writable_dir(path: Path) -> bool:
    """Check writability by actually creating (and removing) a temp file."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".et_write_test_", dir=path)
        os.close(fd)
        os.remove(name)
        return True
    except OSError:
        return False


def resolve_config_dir(
    override: str | Path | None = None,
    *,
    frozen: bool | None = None,
    executable: str | Path | None = None,
    fallback: Path | None = None,
) -> Path:
    """Pick the directory holding settings/state files.

    * ``--config-dir`` override wins.
    * Frozen exe: the exe's directory (portable) if writable.
    * Otherwise / not writable: ``%APPDATA%\\EarnTicker``.
    """
    fallback = fallback if fallback is not None else appdata_dir()
    if override:
        path = Path(override)
        path.mkdir(parents=True, exist_ok=True)
        return path
    if frozen is None:
        frozen = bool(getattr(sys, "frozen", False))
    if frozen:
        exe_dir = Path(executable or sys.executable).resolve().parent
        if is_writable_dir(exe_dir):
            return exe_dir
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


# ---------------------------------------------------------------------------
# load / save


def backup_corrupt(path: Path, now: datetime | None = None) -> Path | None:
    """Copy a corrupt file to ``<name>.<YYYYmmdd-HHMMSS>.bak`` (never overwritten).

    Returns the backup path, or None if the copy failed.
    """
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    target = path.with_name(f"{path.name}.{stamp}.bak")
    n = 1
    while target.exists():
        target = path.with_name(f"{path.name}.{stamp}-{n}.bak")
        n += 1
    try:
        shutil.copyfile(path, target)
    except OSError:
        return None
    return target


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding=READ_ENCODING))


class SettingsLoad(NamedTuple):
    settings: Settings
    existed: bool
    corrupt: bool  # file was unreadable; defaults used
    backup: Path | None  # where the corrupt file was copied to


def read_settings(path: Path) -> SettingsLoad:
    """Load settings and report whether a corrupt file was replaced. Never raises."""
    if not path.exists():
        return SettingsLoad(Settings(), False, False, None)
    try:
        data = read_json(path)
        if not isinstance(data, dict):
            raise ValueError("settings root must be an object")
    except (OSError, ValueError):
        return SettingsLoad(Settings(), True, True, backup_corrupt(path))
    return SettingsLoad(Settings.from_dict(data), True, False, None)


def load_settings(path: Path) -> tuple[Settings, bool]:
    """Load settings; returns ``(settings, existed)``. Never raises.

    A corrupt file is copied to a timestamped ``.bak`` and defaults are returned.
    """
    result = read_settings(path)
    return result.settings, result.existed


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def save_settings(path: Path, settings: Settings) -> None:
    atomic_write_json(path, settings.to_dict())
