"""Offscreen smoke tests for the Qt layer (panel + settings dialog)."""

import os
from datetime import date, datetime
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from earnticker.app import corruption_notice  # noqa: E402
from earnticker.pay import compute_day  # noqa: E402
from earnticker.settings import Settings  # noqa: E402
from earnticker.state import StateStore  # noqa: E402
from earnticker.ui.common import FixedClock, fmt_money  # noqa: E402
from earnticker.ui.panel import Panel  # noqa: E402
from earnticker.ui.settings_dialog import SettingsDialog, config_location_hint  # noqa: E402


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


def test_panel_no_overtime_status_after_auto_cap(app):
    clock = FixedClock(datetime(2026, 10, 19, 20, 14, 59))
    panel = Panel(Settings(), StateStore(None), clock)
    assert panel.status_label.text().startswith("加班中 ×1.25")
    assert panel.overtime_active
    clock.moment = datetime(2026, 10, 19, 20, 15)
    panel.refresh()
    assert panel.status_label.text().startswith("已下班 · 其中加班")
    assert not panel.overtime_active
    assert panel.amount_label.text() == "¥39,304.81"


def test_panel_manual_session_in_work_hours_is_pending(app):
    store = StateStore(None)
    clock = FixedClock(datetime(2026, 10, 19, 15, 0))
    panel = Panel(Settings(), store, clock)
    store.toggle(panel.settings.pay_rules(), clock.now())
    panel.refresh()
    assert panel.status_label.text() == "已开始加班（17:15 起计）"
    assert panel.overtime_active  # menu offers 「结束加班」
    clock.moment = datetime(2026, 10, 19, 17, 15, 1)
    panel.refresh()
    assert panel.status_label.text().startswith("加班中 ×1.25")


def test_panel_rollover_after_sleep_does_not_overcount(app):
    # M1 repro through the panel: last refresh Monday 20:00, wake Tuesday 08:00
    store = StateStore(None)
    clock = FixedClock(datetime(2026, 10, 19, 20, 0))
    panel = Panel(Settings(), store, clock)
    clock.moment = datetime(2026, 10, 20, 8, 0)
    panel.refresh()
    rules = panel.settings.pay_rules()
    mon = date(2026, 10, 19)
    e = compute_day(rules, mon, store.get(mon), clock.now())
    assert e.overtime == pytest.approx(11029.41 + 66.84, abs=0.02)
    assert not store.get(date(2026, 10, 20)).sessions


def test_panel_rollover_while_awake_continues_session(app):
    store = StateStore(None)
    clock = FixedClock(datetime(2026, 10, 17, 23, 0))  # Saturday
    panel = Panel(Settings(), store, clock)
    store.toggle(panel.settings.pay_rules(), clock.now())
    clock.moment = datetime(2026, 10, 17, 23, 59, 59)
    panel.refresh()
    clock.moment = datetime(2026, 10, 18, 0, 0, 0)
    panel.refresh()
    assert store.get(date(2026, 10, 18)).sessions == ((datetime(2026, 10, 18), None),)
    sat = store.get(date(2026, 10, 17))
    assert sat.sessions == ((datetime(2026, 10, 17, 23), datetime(2026, 10, 18)),)
    assert panel.status_label.text().startswith("加班中 ×1.6")


def test_settings_dialog_new_overtime_fields(app):
    dlg = SettingsDialog(Settings())
    assert dlg.auto_cap.value() == 3.0
    assert dlg.coef_rest_late.value() == 1.6
    assert dlg.auto_cap.singleStep() == 0.5
    assert (dlg.auto_cap.minimum(), dlg.auto_cap.maximum()) == (0.0, 12.0)
    dlg.auto_cap.setValue(4.5)
    dlg.coef_rest_late.setValue(1.8)
    s = dlg.collect()
    assert s.auto_overtime_cap_hours == 4.5
    assert s.coef_rest_late_night == 1.8


def test_settings_dialog_import_example_holidays(app):
    dlg = SettingsDialog(Settings(holidays=["2030-01-01"]))
    n = dlg.import_example_holidays(quiet=True)
    assert n > 30
    days = dlg.collect().holidays
    assert "2030-01-01" in days and "2026-01-01" in days and "2027-11-23" in days


def test_config_location_hint_warns_for_onedrive(app):
    plain = config_location_hint(Path("C:/Tools/EarnTicker"))
    assert "Tools" in plain and "OneDrive" not in plain
    warn = config_location_hint(Path("C:/Users/x/OneDrive/Desktop"))
    assert "OneDrive 同步" in warn
    dlg = SettingsDialog(Settings(), Path("C:/Users/x/OneDrive/Desktop"))
    assert "OneDrive 同步" in dlg.config_hint.text()


def test_corruption_notice_text():
    assert corruption_notice(False, False) is None
    assert corruption_notice(True, False) == "设置文件损坏，已备份并恢复默认。"
    assert "加班记录" in corruption_notice(True, True)
