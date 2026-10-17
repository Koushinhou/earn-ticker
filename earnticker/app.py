"""Application controller: wires settings, state, panel, tray and dialog."""

from __future__ import annotations

import argparse
import contextlib
import sys
import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QLockFile, QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from . import autostart
from .settings import (
    SETTINGS_FILE,
    STATE_FILE,
    Settings,
    read_settings,
    resolve_config_dir,
    save_settings,
)
from .state import StateStore
from .ui.common import Clock, app_font, coin_icon
from .ui.panel import Panel
from .ui.settings_dialog import SettingsDialog
from .ui.tray import Tray

AUTOSTART_FAILED = "无法修改「启动」文件夹中的快捷方式（开机自启需要 PowerShell，可能已被禁用）。"


class OffsetClock(Clock):
    """Real clock shifted so that it started at ``start`` (demo mode)."""

    def __init__(self, start: datetime) -> None:
        self.offset = start - datetime.now()

    def now(self) -> datetime:
        return (datetime.now() + self.offset).replace(microsecond=0)


def corruption_notice(settings_corrupt: bool, state_corrupt: bool) -> str | None:
    """One-time message after a corrupt JSON file was backed up and replaced."""
    parts = []
    if settings_corrupt:
        parts.append("设置文件损坏，已备份并恢复默认")
    if state_corrupt:
        parts.append("加班记录文件损坏，已备份并重新开始记录")
    return "；".join(parts) + "。" if parts else None


class _Bridge(QObject):
    """Delivers results from worker threads to the GUI thread."""

    autostart_refreshed = Signal(bool)


class Controller:
    def __init__(self, config_dir: Path, clock: Clock, *, use_tray: bool = True) -> None:
        self.config_dir = config_dir
        self.settings_path = config_dir / SETTINGS_FILE
        loaded = read_settings(self.settings_path)
        self.settings = loaded.settings
        self.first_run = not loaded.existed
        self.store = StateStore(config_dir / STATE_FILE)
        self.store.load()
        self.notice = corruption_notice(loaded.corrupt, self.store.corrupt)
        self._bridge = _Bridge()
        self._bridge.autostart_refreshed.connect(self._autostart_refreshed)
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
            self.tray = Tray(self.panel.isVisible, lambda: self.panel.overtime_active)
            self.tray.toggle_panel_requested.connect(self.toggle_panel)
            self.tray.overtime_toggle_requested.connect(self.toggle_overtime)
            self.tray.settings_requested.connect(self.open_settings)
            self.tray.quit_requested.connect(self.quit)
            self.tray.show()

    def start(self) -> None:
        self.panel.restore_position()
        self.panel.show()
        self.panel.start()
        if self.first_run or self.notice:
            self._save()
        if self.notice:
            QTimer.singleShot(500, lambda: self.show_message(self.notice or ""))
        if self.first_run:
            QTimer.singleShot(300, self.open_settings)
        if self.settings.autostart and getattr(sys, "frozen", False):
            self._refresh_autostart_async()

    def show_message(self, text: str, warning: bool = True) -> None:
        """Tray balloon if available, otherwise a message box."""
        icon = (
            QSystemTrayIcon.MessageIcon.Warning
            if warning
            else QSystemTrayIcon.MessageIcon.Information
        )
        if self.tray is not None and self.tray.supportsMessages():
            self.tray.showMessage("EarnTicker", text, icon, 8000)
        else:
            QMessageBox.warning(self.panel, "EarnTicker", text)

    def _refresh_autostart_async(self) -> None:
        """Re-point the Startup shortcut at this exe (PowerShell runs off the GUI thread)."""

        def work() -> None:
            try:
                ok = autostart.refresh()
            except Exception:  # never let a background check crash the app
                ok = False
            self._bridge.autostart_refreshed.emit(ok)

        threading.Thread(target=work, name="autostart-refresh", daemon=True).start()

    def _autostart_refreshed(self, ok: bool) -> None:
        if not ok:
            self.show_message(AUTOSTART_FAILED)

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
            try:
                ok = autostart.apply(s.autostart)
            except Exception:
                ok = False
            if not ok:
                QMessageBox.warning(self.dialog or self.panel, "开机自启", AUTOSTART_FAILED)
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
