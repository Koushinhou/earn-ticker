"""Locate files bundled with the app (repo checkout or PyInstaller exe)."""

from __future__ import annotations

import sys
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parent.parent
HOLIDAYS_EXAMPLE = "config/holidays.example.json"


def resource_path(
    relative: str, *, frozen: bool | None = None, bundle_dir: str | Path | None = None
) -> Path:
    """Absolute path of a bundled data file.

    Frozen (one-file exe): files listed in ``EarnTicker.spec`` ``datas`` are
    unpacked to ``sys._MEIPASS``. From source: relative to the repo root.
    """
    if frozen is None:
        frozen = bool(getattr(sys, "frozen", False))
    if frozen:
        base = Path(bundle_dir or getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    else:
        base = SOURCE_ROOT
    return base / relative
