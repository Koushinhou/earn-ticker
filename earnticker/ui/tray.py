"""System tray icon (gold coin) with the main menu."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from .common import app_font, coin_icon


class Tray(QSystemTrayIcon):
    toggle_panel_requested = Signal()
    overtime_toggle_requested = Signal()
    settings_requested = Signal()
    quit_requested = Signal()

    def __init__(
        self, is_panel_visible: Callable[[], bool], is_overtime_running: Callable[[], bool]
    ) -> None:
        super().__init__(coin_icon())
        self.setToolTip("EarnTicker")
        self._is_panel_visible = is_panel_visible
        self._is_overtime_running = is_overtime_running

        menu = QMenu()
        menu.setFont(app_font(point_size=9.5))
        self.act_toggle = QAction("显示/隐藏", menu)
        self.act_toggle.triggered.connect(self.toggle_panel_requested.emit)
        self.act_overtime = QAction("开始加班", menu)
        self.act_overtime.triggered.connect(self.overtime_toggle_requested.emit)
        act_settings = QAction("设置…", menu)
        act_settings.triggered.connect(self.settings_requested.emit)
        act_quit = QAction("退出", menu)
        act_quit.triggered.connect(self.quit_requested.emit)
        menu.addAction(self.act_toggle)
        menu.addAction(self.act_overtime)
        menu.addAction(act_settings)
        menu.addSeparator()
        menu.addAction(act_quit)
        menu.aboutToShow.connect(self._update_texts)
        self._menu = menu
        self.setContextMenu(menu)
        self.activated.connect(self._on_activated)

    def _update_texts(self) -> None:
        self.act_toggle.setText("隐藏面板" if self._is_panel_visible() else "显示面板")
        self.act_overtime.setText("结束加班" if self._is_overtime_running() else "开始加班")

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.toggle_panel_requested.emit()
