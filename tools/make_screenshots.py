"""Generate README screenshots with example data and a fixed fake clock.

Usage:  .venv\\Scripts\\python tools\\make_screenshots.py

Uses a temporary config dir with example settings (annual income 6,000,000)
so no real data can leak into the images.
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPoint, QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from earnticker.settings import (  # noqa: E402
    SETTINGS_FILE,
    Settings,
    load_settings,
    parse_holiday_file,
    save_settings,
)
from earnticker.state import StateStore  # noqa: E402
from earnticker.ui.common import FixedClock, app_font  # noqa: E402
from earnticker.ui.panel import Panel  # noqa: E402
from earnticker.ui.settings_dialog import SettingsDialog  # noqa: E402

DOCS = ROOT / "docs"
WEEKDAY = datetime(2026, 10, 19, 14, 30, 0)  # Monday
OVERTIME = datetime(2026, 10, 19, 18, 30, 0)
REST_DAY = datetime(2026, 10, 17, 11, 0, 0)  # Saturday


def on_backdrop(widget: QWidget, pad: int = 28) -> QPixmap:
    """Composite the translucent panel onto a desktop-like gradient."""
    shot = widget.grab()
    dpr = shot.devicePixelRatio()
    w = int(shot.width() / dpr) + 2 * pad
    h = int(shot.height() / dpr) + 2 * pad
    out = QPixmap(int(w * dpr), int(h * dpr))
    out.setDevicePixelRatio(dpr)
    p = QPainter(out)
    grad = QLinearGradient(0, 0, w, h)
    grad.setColorAt(0.0, QColor("#3A6073"))
    grad.setColorAt(1.0, QColor("#16222A"))
    p.fillRect(QRect(0, 0, w, h), grad)
    p.drawPixmap(QPoint(pad, pad), shot)
    p.end()
    return out


def make_panel(settings: Settings, moment: datetime, store: StateStore) -> Panel:
    clock = FixedClock(moment - timedelta(seconds=1))
    panel = Panel(settings, store, clock)
    panel.show()
    panel.refresh()
    clock.moment = moment
    panel.refresh()  # second refresh produces the "+¥0.89" tick label
    panel._float_anim.stop()  # freeze the tick label fully visible
    panel.float_effect.setOpacity(1.0)
    QApplication.processEvents()
    return panel


def main() -> int:
    app = QApplication(sys.argv[:1])
    app.setFont(app_font(point_size=9.5))
    DOCS.mkdir(exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        cfg = Path(tmp)
        base, _ = load_settings(ROOT / "config" / "settings.example.json")
        holidays = json.loads((ROOT / "config" / "holidays.example.json").read_text("utf-8"))
        base.holidays = parse_holiday_file(holidays)
        save_settings(cfg / SETTINGS_FILE, base)
        store = StateStore(None)

        panel = make_panel(base, WEEKDAY, store)
        on_backdrop(panel).save(str(DOCS / "panel.png"))
        panel.close()

        panel = make_panel(base, OVERTIME, store)
        on_backdrop(panel).save(str(DOCS / "panel_overtime.png"))
        panel.close()

        panel = make_panel(base, REST_DAY, store)
        on_backdrop(panel).save(str(DOCS / "panel_rest.png"))
        panel.close()

        custom = base.copy()
        custom.amount_color = "#7CFC9A"
        custom.clock_color = "#FFFFFF"
        custom.font_size = 38
        custom.opacity = 0.6
        custom.show_month_total = True
        custom.show_year_total = True
        panel = make_panel(custom, WEEKDAY, store)
        on_backdrop(panel).save(str(DOCS / "panel_custom_color.png"))
        panel.close()

        dlg = SettingsDialog(base, cfg)
        dlg.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, False)
        dlg.show()
        QApplication.processEvents()
        dlg.grab().save(str(DOCS / "settings.png"))
        dlg.tabs.setCurrentIndex(4)
        QApplication.processEvents()
        dlg.grab().save(str(DOCS / "settings_appearance.png"))
        dlg.close()

    for name in (
        "panel.png",
        "panel_overtime.png",
        "panel_rest.png",
        "panel_custom_color.png",
        "settings.png",
        "settings_appearance.png",
    ):
        print("wrote", DOCS / name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
