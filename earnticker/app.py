"""Application controller: wires settings, state, panel, tray and dialog."""

from __future__ import annotations

import argparse
import contextlib
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QLockFile, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from . import autostart
from .settings import (
    SETTINGS_FILE,
    STATE_FILE,
    Settings,
    load_settings,
    resolve_config_dir,
    save_settings,
)
from .state import StateStore
from .ui.common import Clock, app_font, coin_icon
from .ui.panel import Panel
from .ui.settings_dialog import SettingsDialog
from .ui.tray import Tray


class OffsetClock(Clock):
    """Real clock shifted so that it started at ``start`` (demo mode)."""

    def __init__(self, start: datetime) -> None:
        self.offset = start - datetime.now()

    def now(self) -> datetime:
        return (datetime.now() + self.offset).replace(microsecond=0)


class Controller:
    def __init__(self, config_dir: Path, clock: Clock, *, use_tray: bool = True) -> None:
        self.config_dir = config_dir
        self.settings_path = config_dir / SETTINGS_FILE
        self.settings, existed = load_settings(self.settings_path)
        self.first_run = not existed
        self.store = StateStore(config_dir / STATE_FILE)
        self.store.load()
        self.clock = clock
        self.dialog: SettingsDialog | None = None

        self.panel = Panel(self.settings, self.store, clock)
        self.panel.settings_requested.connect(self.open_settings)
        self.panel.overtime_toggle_requested.connect(self.toggle_overtime)
        self.panel.hide_requested.connect(self.panel.hide)
        self.panel.quit_requested.connect(self.quit)
        self.panel.position_changed.connect(self.save_position)

        self.tray: Tray | None = None
        if use_tray and Tray.isSystemTrayAvailable():
            self.tray = Tray(self.panel.isVisible, lambda: self.panel.overtime_running)
            self.tray.toggle_panel_requested.connect(self.toggle_panel)
            self.tray.overtime_toggle_requested.connect(self.toggle_overtime)
            self.tray.settings_requested.connect(self.open_settings)
            self.tray.quit_requested.connect(self.quit)
            self.tray.show()

    def start(self) -> None:
        self.panel.restore_position()
        self.panel.show()
        self.panel.start()
        if self.first_run:
            save_settings(self.settings_path, self.settings)
            QTimer.singleShot(300, self.open_settings)

    # -- actions ------------------------------------------------------------

    def toggle_panel(self) -> None:
        if self.panel.isVisible():
            self.panel.hide()
        else:
            self.panel.show()
            self.panel.raise_()

    def toggle_overtime(self) -> None:
        self.store.toggle(self.settings.pay_rules(), self.clock.now())
        self.panel.refresh()

    def save_position(self, x: int, y: int) -> None:
        self.settings.panel_x = x
        self.settings.panel_y = y
        self._save()

    def open_settings(self) -> None:
        if self.dialog is not None:
            self.dialog.show()
            self.dialog.raise_()
            self.dialog.activateWindow()
            return
        dlg = SettingsDialog(self.settings, self.config_dir)
        dlg.preview.connect(self._preview)
        dlg.applied.connect(self._apply)
        dlg.finished.connect(self._dialog_closed)
        self.dialog = dlg
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _dialog_closed(self, _result: int) -> None:
        if self.dialog is not None:
            self.dialog.deleteLater()
        self.dialog = None

    def _with_position(self, s: Settings) -> Settings:
        s.panel_x = self.settings.panel_x
        s.panel_y = self.settings.panel_y
        return s

    def _preview(self, s: Settings) -> None:
        self.panel.apply_settings(self._with_position(s))

    def _apply(self, s: Settings) -> None:
        old_autostart = self.settings.autostart
        self.settings = self._with_position(s)
        if s.autostart != old_autostart or s.autostart != autostart.is_enabled():
            ok = autostart.apply(s.autostart)
            if not ok and self.dialog is not None:
                QMessageBox.warning(self.dialog, "开机自启", "无法修改「启动」文件夹中的快捷方式。")
        self.panel.apply_settings(self.settings)
        self._save()

    def _save(self) -> None:
        with contextlib.suppress(OSError):
            save_settings(self.settings_path, self.settings)

    def quit(self) -> None:
        self.store.touch(self.clock.now())
        self.store.save()
        if self.tray is not None:
            self.tray.hide()
        QApplication.quit()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="EarnTicker")
    parser.add_argument("--config-dir", help="directory for settings/state JSON files")
    parser.add_argument("--fake-now", help=argparse.SUPPRESS)  # demo: start clock at ISO time
    parser.add_argument("--quit-after", type=float, help=argparse.SUPPRESS)  # smoke tests
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    app = QApplication(sys.argv[:1])
    app.setApplicationName("EarnTicker")
    app.setQuitOnLastWindowClosed(False)
    app.setFont(app_font(point_size=9.5))
    app.setWindowIcon(coin_icon())

    config_dir = resolve_config_dir(args.config_dir)
    lock = QLockFile(str(config_dir / "earnticker.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.information(None, "EarnTicker", "EarnTicker 已经在运行（请查看系统托盘）。")
        return 0

    clock: Clock = OffsetClock(datetime.fromisoformat(args.fake_now)) if args.fake_now else Clock()
    controller = Controller(config_dir, clock)
    controller.start()
    if args.quit_after:
        QTimer.singleShot(int(args.quit_after * 1000), controller.quit)
    code = app.exec()
    lock.unlock()
    return code
