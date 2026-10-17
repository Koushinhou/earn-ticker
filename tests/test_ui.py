"""Offscreen smoke tests for the Qt layer (panel + settings dialog)."""

import os
from datetime import datetime

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from earnticker.settings import Settings  # noqa: E402
from earnticker.state import StateStore  # noqa: E402
from earnticker.ui.common import FixedClock, fmt_money  # noqa: E402
from earnticker.ui.panel import Panel  # noqa: E402
from earnticker.ui.settings_dialog import SettingsDialog  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_fmt_money():
    assert fmt_money("¥", 18449.198, 2) == "¥18,449.20"
    assert fmt_money("¥", 318449.4, 0) == "¥318,449"


def test_panel_texts_and_tick(app):
    clock = FixedClock(datetime(2026, 10, 19, 14, 29, 59))
    panel = Panel(Settings(), StateStore(None), clock)
    clock.moment = datetime(2026, 10, 19, 14, 30, 0)
    panel.refresh()
    assert panel.clock_label.text() == "14:30:00"
    assert panel.amount_label.text() == "¥18,449.20"
    assert panel.float_label.text() == "+¥0.89"
    assert not panel.status_label.isVisibleTo(panel)


def test_panel_status_lines(app):
    clock = FixedClock(datetime(2026, 10, 19, 8, 0))
    panel = Panel(Settings(), StateStore(None), clock)
    assert panel.status_label.text() == "还没上班"
    assert panel.amount_label.text() == "¥0.00"
    clock.moment = datetime(2026, 10, 19, 18, 30)
    panel.refresh()
    assert panel.status_label.text().startswith("加班中 ×1.25")
    clock.moment = datetime(2026, 10, 17, 11, 0)  # Saturday
    panel.refresh()
    assert panel.status_label.text() == "今天休息"
    assert panel.month_label.isVisibleTo(panel)


def test_settings_preview_applies_color_instantly(app):
    panel = Panel(Settings(), StateStore(None), FixedClock(datetime(2026, 10, 19, 14, 30)))
    dlg = SettingsDialog(Settings())
    dlg.preview.connect(panel.apply_settings)
    dlg.income.setValue(12_000_000)
    assert panel.settings.annual_income == 12_000_000
    assert panel.amount_label.text() == "¥36,898.40"
    dlg._current.amount_color = "#00ff00"
    dlg._changed()
    assert "#00ff00" in panel.amount_label.styleSheet()
    applied = []
    dlg.applied.connect(applied.append)
    dlg._apply()
    assert applied and applied[0].annual_income == 12_000_000
    dlg.income.setValue(1)
    dlg.reject()  # cancel reverts preview to last applied settings
    assert panel.settings.annual_income == 12_000_000
