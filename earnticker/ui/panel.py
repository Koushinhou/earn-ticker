"""The frameless, always-on-top floating panel."""

from __future__ import annotations

from datetime import datetime, timedelta

from PySide6.QtCore import (
    QEasingCurve,
    QParallelAnimationGroup,
    QPoint,
    QPropertyAnimation,
    QRectF,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QAction, QColor, QFontMetrics, QGuiApplication, QPainter
from PySide6.QtWidgets import QGraphicsOpacityEffect, QLabel, QMenu, QVBoxLayout, QWidget

from ..pay import compute_day, month_to_date, year_to_date
from ..settings import Settings
from ..state import StateStore
from .common import Clock, app_font, fmt_money

TOUCH_INTERVAL = timedelta(seconds=60)


class Panel(QWidget):
    settings_requested = Signal()
    overtime_toggle_requested = Signal()
    hide_requested = Signal()
    quit_requested = Signal()
    position_changed = Signal(int, int)

    def __init__(self, settings: Settings, store: StateStore, clock: Clock) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("EarnTicker")
        self.settings = settings
        self.store = store
        self.clock = clock
        self._drag_offset: QPoint | None = None
        self._prev: tuple[datetime, float] | None = None
        self._last_date = None
        self._last_touch: datetime | None = None
        self.overtime_running = False

        self.clock_label = QLabel(self)
        self.amount_label = QLabel(self)
        self.status_label = QLabel(self)
        self.month_label = QLabel(self)
        self.year_label = QLabel(self)
        for lbl in (
            self.clock_label,
            self.amount_label,
            self.status_label,
            self.month_label,
            self.year_label,
        ):
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        layout = QVBoxLayout(self)
        layout.setSpacing(2)
        layout.addWidget(self.clock_label)
        layout.addWidget(self.amount_label)
        layout.addWidget(self.status_label)
        layout.addWidget(self.month_label)
        layout.addWidget(self.year_label)

        # floating "+¥0.89" label, reused for every tick (no pile-up)
        self.float_label = QLabel(self)
        self.float_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.float_effect = QGraphicsOpacityEffect(self.float_label)
        self.float_effect.setOpacity(0.0)
        self.float_label.setGraphicsEffect(self.float_effect)
        self._fade = QPropertyAnimation(self.float_effect, b"opacity", self)
        self._fade.setDuration(950)
        self._fade.setKeyValueAt(0.0, 1.0)
        self._fade.setKeyValueAt(0.6, 0.85)
        self._fade.setKeyValueAt(1.0, 0.0)
        self._drift = QPropertyAnimation(self.float_label, b"pos", self)
        self._drift.setDuration(950)
        self._drift.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._float_anim = QParallelAnimationGroup(self)
        self._float_anim.addAnimation(self._fade)
        self._float_anim.addAnimation(self._drift)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._on_timer)

        self.apply_settings(settings)

    # -- settings / appearance ------------------------------------------

    def apply_settings(self, settings: Settings) -> None:
        """Apply (possibly preview) settings immediately."""
        self.settings = settings
        fs = settings.font_size
        self.clock_label.setFont(app_font(pixel_size=round(fs * 0.5)))
        amount_font = app_font(pixel_size=fs)
        amount_font.setBold(True)
        self.amount_label.setFont(amount_font)
        small = app_font(pixel_size=max(11, round(fs * 0.42)))
        for lbl in (self.status_label, self.month_label, self.year_label):
            lbl.setFont(small)
        float_font = app_font(pixel_size=max(11, round(fs * 0.45)))
        float_font.setBold(True)
        self.float_label.setFont(float_font)

        clock_color = QColor(settings.clock_color).name()
        amount_color = QColor(settings.amount_color).name()
        self.clock_label.setStyleSheet(f"color: {clock_color};")
        self.amount_label.setStyleSheet(f"color: {amount_color};")
        for lbl in (self.status_label, self.month_label, self.year_label):
            lbl.setStyleSheet(f"color: {clock_color};")
        self.float_label.setStyleSheet(f"color: {amount_color};")
        m = max(6, round(fs * 0.3))
        self.layout().setContentsMargins(m + 4, m, m + 4, m)
        self._prev = None  # settings change must not produce a fake tick
        self.refresh()
        self.update()

    # -- live update ----------------------------------------------------------

    def start(self) -> None:
        self.refresh()
        self._schedule()

    def _schedule(self) -> None:
        now = datetime.now()
        delay = 1000 - now.microsecond // 1000 + 3
        self._timer.start(delay)

    def _on_timer(self) -> None:
        self.refresh()
        self._schedule()

    def refresh(self) -> None:
        s = self.settings
        rules = s.pay_rules()
        now = self.clock.now()
        today = now.date()

        if self._last_date is not None and today != self._last_date:
            self.store.rollover(rules, self._last_date, today)
            self._prev = None
        self._last_date = today
        if self._last_touch is None or abs(now - self._last_touch) >= TOUCH_INTERVAL:
            self.store.touch(now)
            self.store.save()
            self._last_touch = now

        records = self.store.records
        day = compute_day(rules, today, records.get(today), now)
        self.overtime_running = day.overtime_running
        sym = s.currency_symbol
        total = day.total

        self.clock_label.setText(now.strftime("%H:%M:%S"))
        self.amount_label.setText(fmt_money(sym, total, s.today_decimals))

        status = ""
        if day.overtime_running:
            status = f"加班中 ×{day.current_coef:g} · 其中加班 {fmt_money(sym, day.overtime, 2)}"
        elif day.phase == "rest":
            status = "今天休息"
            if day.overtime > 0:
                status += f" · 加班 {fmt_money(sym, day.overtime, 2)}"
        elif day.phase == "before":
            status = "还没上班"
        elif day.phase == "after":
            status = "已下班"
            if day.overtime > 0:
                status += f" · 其中加班 {fmt_money(sym, day.overtime, 2)}"
        self.status_label.setText(status)
        self.status_label.setVisible(bool(status))

        show_month = s.show_month_total or not day.is_work_day
        if show_month:
            mtd = month_to_date(rules, now, records)
            self.month_label.setText(f"本月累计 {fmt_money(sym, mtd, s.total_decimals)}")
        self.month_label.setVisible(show_month)
        if s.show_year_total:
            ytd = year_to_date(rules, now, records)
            self.year_label.setText(f"今年累计 {fmt_money(sym, ytd, s.total_decimals)}")
        self.year_label.setVisible(s.show_year_total)

        self._fit_size()

        if self._prev is not None:
            prev_now, prev_total = self._prev
            delta = total - prev_total
            if delta > 0.0 and timedelta(0) < now - prev_now <= timedelta(seconds=2):
                self.show_tick(delta)
        self._prev = (now, total)

    def _fit_size(self) -> None:
        hint = self.layout().sizeHint()
        width = max(self.settings.panel_width, hint.width() + self._float_room())
        self.setFixedSize(width, hint.height())

    def _float_room(self) -> int:
        fm = QFontMetrics(self.float_label.font())
        return 2 * (fm.horizontalAdvance("+¥0.00") + 8)

    def show_tick(self, delta: float) -> None:
        """Show the drifting ``+¥0.89`` label next to the amount."""
        self.float_label.setText(f"+{fmt_money(self.settings.currency_symbol, delta, 2)}")
        self.float_label.adjustSize()
        geo = self.amount_label.geometry()
        fm = QFontMetrics(self.amount_label.font())
        text_w = fm.horizontalAdvance(self.amount_label.text())
        x = geo.center().x() + text_w // 2 + 6
        x = min(x, self.width() - self.float_label.width() - 4)
        start = QPoint(x, geo.top() + max(0, (geo.height() - self.float_label.height()) // 2))
        end = QPoint(x, max(0, start.y() - max(14, geo.height() // 2)))
        self._float_anim.stop()
        self._drift.setStartValue(start)
        self._drift.setEndValue(end)
        self.float_label.move(start)
        self.float_label.show()
        self.float_label.raise_()
        self._float_anim.start()

    # -- painting -------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt API)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        alpha = round(255 * self.settings.opacity)
        p.setBrush(QColor(18, 18, 22, alpha))
        p.setPen(QColor(255, 255, 255, min(alpha, 40)))
        radius = max(8.0, self.settings.font_size * 0.4)
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
        p.end()

    # -- position / dragging --------------------------------------------------

    def restore_position(self) -> None:
        s = self.settings
        if s.panel_x is not None and s.panel_y is not None:
            point = QPoint(s.panel_x, s.panel_y)
            if QGuiApplication.screenAt(point + QPoint(10, 10)) is not None:
                self.move(point)
                return
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            self.move(area.right() - self.width() - 24, area.top() + 24)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._drag_offset is not None:
            self._drag_offset = None
            pos = self.pos()
            self.position_changed.emit(pos.x(), pos.y())
            event.accept()

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        menu = QMenu(self)
        menu.setFont(app_font(point_size=9.5))
        act_settings = QAction("设置…", menu)
        act_settings.triggered.connect(self.settings_requested.emit)
        act_ot = QAction("结束加班" if self.overtime_running else "开始加班", menu)
        act_ot.triggered.connect(self.overtime_toggle_requested.emit)
        act_hide = QAction("收起到托盘", menu)
        act_hide.triggered.connect(self.hide_requested.emit)
        act_quit = QAction("退出", menu)
        act_quit.triggered.connect(self.quit_requested.emit)
        menu.addAction(act_settings)
        menu.addAction(act_ot)
        menu.addSeparator()
        menu.addAction(act_hide)
        menu.addAction(act_quit)
        menu.exec(event.globalPos())
