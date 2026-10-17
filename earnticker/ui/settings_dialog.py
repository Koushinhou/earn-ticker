"""Settings dialog. Every change is previewed on the panel immediately."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from PySide6.QtCore import QDate, Qt, QTime, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractButton,
    QCheckBox,
    QColorDialog,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QTabWidget,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from ..resources import HOLIDAYS_EXAMPLE, resource_path
from ..settings import (
    CAP_RANGE,
    COEF_RANGE,
    MAX_ANNUAL_INCOME,
    Settings,
    parse_hhmm,
    parse_holiday_file,
    read_json,
)
from .common import app_font, coin_icon, fmt_money

WEEKDAY_NAMES = ["一", "二", "三", "四", "五", "六", "日"]


def config_location_hint(config_dir: Path | None) -> str:
    """Text for the 「其他」 tab: where settings live, plus a OneDrive warning."""
    if config_dir is None:
        return "设置保存位置：（未保存）"
    text = f"设置保存位置：\n{config_dir}"
    if "onedrive" in str(config_dir).lower():
        text += (
            "\n\n⚠ 该文件夹由 OneDrive 同步，含年收入的设置文件会被上传到云端。"
            "建议把 EarnTicker.exe 放到不同步的文件夹（如 C:\\Tools\\EarnTicker\\）。"
        )
    return text


class SettingsDialog(QDialog):
    preview = Signal(object)  # Settings, not yet saved
    applied = Signal(object)  # Settings, to be saved

    def __init__(
        self, settings: Settings, config_dir: Path | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("EarnTicker 设置")
        self.setWindowIcon(coin_icon())
        self.setFont(app_font(point_size=9.5))
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self._original = settings.copy()
        self._current = settings.copy()
        self._loading = True

        tabs = QTabWidget(self)
        tabs.addTab(self._build_income_tab(), "收入")
        tabs.addTab(self._build_time_tab(), "时间")
        tabs.addTab(self._build_overtime_tab(), "加班")
        tabs.addTab(self._build_holiday_tab(), "假日")
        tabs.addTab(self._build_look_tab(), "外观")
        tabs.addTab(self._build_other_tab(config_dir), "其他")
        self.tabs = tabs

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Apply,
            self,
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.button(QDialogButtonBox.StandardButton.Apply).setText("应用")
        buttons.clicked.connect(self._on_button)
        self.buttons = buttons

        root = QVBoxLayout(self)
        root.addWidget(tabs)
        root.addWidget(buttons)

        self._load(settings)
        self._loading = False
        self._update_summary()
        self.resize(500, 440)

    # -- tabs -------------------------------------------------------------

    def _spin(self, lo: int, hi: int, suffix: str = "") -> QSpinBox:
        sb = QSpinBox()
        sb.setRange(lo, hi)
        sb.setSuffix(suffix)
        sb.valueChanged.connect(self._changed)
        return sb

    def _dspin(self, lo: float, hi: float, step: float, decimals: int) -> QDoubleSpinBox:
        sb = QDoubleSpinBox()
        sb.setRange(lo, hi)
        sb.setSingleStep(step)
        sb.setDecimals(decimals)
        sb.valueChanged.connect(self._changed)
        return sb

    def _check(self, text: str) -> QCheckBox:
        cb = QCheckBox(text)
        cb.toggled.connect(self._changed)
        return cb

    def _build_income_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        self.income = self._dspin(0, MAX_ANNUAL_INCOME, 100_000, 0)
        self.income.setGroupSeparatorShown(True)
        self.currency = QLineEdit()
        self.currency.setMaxLength(4)
        self.currency.textChanged.connect(self._changed)
        self.days_in_year = self._spin(1, 366, " 天")
        self.rest_days = self._spin(0, 366, " 天")
        self.leave_days = self._spin(0, 366, " 天")
        self.take_home = self._check("显示到手金额（所有金额 × 比例）")
        self.take_home_ratio = self._spin(1, 100, " %")
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow("年收入（税前）", self.income)
        form.addRow("货币符号", self.currency)
        form.addRow("一年天数", self.days_in_year)
        form.addRow("休息日", self.rest_days)
        form.addRow("年假", self.leave_days)
        form.addRow(self.take_home)
        form.addRow("到手比例", self.take_home_ratio)
        form.addRow(self.summary)
        return w

    def _build_time_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        self.start_time = QTimeEdit()
        self.end_time = QTimeEdit()
        for te in (self.start_time, self.end_time):
            te.setDisplayFormat("HH:mm")
            te.timeChanged.connect(self._changed)
        self.hours_label = QLabel()
        row = QHBoxLayout()
        self.weekday_checks: list[QCheckBox] = []
        for name in WEEKDAY_NAMES:
            cb = self._check(name)
            self.weekday_checks.append(cb)
            row.addWidget(cb)
        row.addStretch(1)
        form.addRow("上班时间", self.start_time)
        form.addRow("下班时间", self.end_time)
        form.addRow("", self.hours_label)
        form.addRow("工作日（周）", row)
        note = QLabel("不扣午休。休息日 = 不在上面勾选的星期 + 「假日」列表中的日期。")
        note.setWordWrap(True)
        form.addRow(note)
        return w

    def _build_overtime_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        self.auto_overtime = self._check("下班后自动计算加班")
        self.auto_cap = self._dspin(*CAP_RANGE, 0.5, 1)
        self.auto_cap.setSuffix(" 小时")
        self.coef_weekday = self._dspin(*COEF_RANGE, 0.05, 2)
        self.coef_late = self._dspin(*COEF_RANGE, 0.05, 2)
        self.coef_rest = self._dspin(*COEF_RANGE, 0.05, 2)
        self.coef_rest_late = self._dspin(*COEF_RANGE, 0.05, 2)
        for sb in (self.coef_weekday, self.coef_late, self.coef_rest, self.coef_rest_late):
            sb.setPrefix("× ")
        form.addRow(self.auto_overtime)
        form.addRow("自动加班上限（下班后）", self.auto_cap)
        form.addRow("平日加班系数", self.coef_weekday)
        form.addRow("平日深夜系数（22:00–05:00）", self.coef_late)
        form.addRow("休息日系数", self.coef_rest)
        form.addRow("休息日深夜系数", self.coef_rest_late)
        note = QLabel(
            "自动加班最多计到「下班时间 + 上限」（默认 17:15 + 3 小时 = 20:15），"
            "提前点「结束加班」则到点击时为止。"
            "手动「开始/结束加班」（面板右键菜单或托盘菜单，可多次）不受上限限制；"
            "上班时间内开始的手动加班从下班时间起计。"
            "系数不叠加。加班按天结算，到午夜 0 点截止（之后算到第二天）。"
        )
        note.setWordWrap(True)
        form.addRow(note)
        return w

    def _build_holiday_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        self.holiday_list = QListWidget()
        self.holiday_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        add_row = QHBoxLayout()
        self.holiday_date = QDateEdit(QDate.currentDate())
        self.holiday_date.setCalendarPopup(True)
        self.holiday_date.setDisplayFormat("yyyy-MM-dd")
        btn_add = QPushButton("添加")
        btn_add.clicked.connect(self._add_holiday)
        btn_del = QPushButton("删除选中")
        btn_del.clicked.connect(self._remove_holidays)
        btn_import = QPushButton("导入 JSON…")
        btn_import_jp = QPushButton("导入日本假日（2026–2027 示例）")
        btn_import_jp.clicked.connect(lambda: self.import_example_holidays())
        btn_import.clicked.connect(self._import_holidays)
        btn_clear = QPushButton("清空")
        btn_clear.clicked.connect(self._clear_holidays)
        add_row.addWidget(self.holiday_date)
        add_row.addWidget(btn_add)
        add_row.addWidget(btn_del)
        add_row.addStretch(1)
        add_row.addWidget(btn_import)
        add_row.addWidget(btn_clear)
        v.addWidget(QLabel("假日（这些日期按休息日处理）："))
        v.addWidget(self.holiday_list)
        v.addLayout(add_row)
        jp_row = QHBoxLayout()
        jp_row.addWidget(btn_import_jp)
        jp_row.addStretch(1)
        v.addLayout(jp_row)
        return w

    def _build_look_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        self.amount_color_btn = QPushButton()
        self.amount_color_btn.clicked.connect(lambda: self._pick_color("amount_color"))
        self.clock_color_btn = QPushButton()
        self.clock_color_btn.clicked.connect(lambda: self._pick_color("clock_color"))
        self.font_size = self._spin(10, 120, " px")
        self.panel_width = self._spin(80, 2000, " px")
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(0, 100)
        self.opacity.valueChanged.connect(self._changed)
        self.opacity_label = QLabel()
        op_row = QHBoxLayout()
        op_row.addWidget(self.opacity, 1)
        op_row.addWidget(self.opacity_label)
        self.today_decimals = self._spin(0, 4)
        self.total_decimals = self._spin(0, 4)
        self.show_month = self._check("面板显示本月累计")
        self.show_year = self._check("面板显示今年累计")
        form.addRow("金额颜色", self.amount_color_btn)
        form.addRow("时钟颜色", self.clock_color_btn)
        form.addRow("金额字号", self.font_size)
        form.addRow("面板最小宽度", self.panel_width)
        form.addRow("背景不透明度", op_row)
        form.addRow("今日金额小数位", self.today_decimals)
        form.addRow("累计金额小数位", self.total_decimals)
        form.addRow(self.show_month)
        form.addRow(self.show_year)
        return w

    def _build_other_tab(self, config_dir: Path | None) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        self.autostart = self._check("开机自动启动（在「启动」文件夹创建快捷方式）")
        v.addWidget(self.autostart)
        self.config_hint = QLabel(config_location_hint(config_dir))
        self.config_hint.setWordWrap(True)
        self.config_hint.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(self.config_hint)
        info = QLabel(
            "EarnTicker 完全离线运行：不联网、不上传、不写注册表、不需要管理员权限。"
            "年收入等数据只保存在本机（如果所在文件夹被 OneDrive 等同步，则会随之同步）。"
            "开机自启通过 PowerShell 创建快捷方式；若 PowerShell 被禁用则可能失败。"
        )
        info.setWordWrap(True)
        v.addWidget(info)
        v.addStretch(1)
        return w

    # -- load / collect ----------------------------------------------------

    def _load(self, s: Settings) -> None:
        self.income.setValue(s.annual_income)
        self.currency.setText(s.currency_symbol)
        self.days_in_year.setValue(s.days_in_year)
        self.rest_days.setValue(s.rest_days)
        self.leave_days.setValue(s.annual_leave_days)
        self.take_home.setChecked(s.take_home_enabled)
        self.take_home_ratio.setValue(round(s.take_home_ratio * 100))
        st = parse_hhmm(s.start_time)
        et = parse_hhmm(s.end_time)
        if st:
            self.start_time.setTime(QTime(st.hour, st.minute))
        if et:
            self.end_time.setTime(QTime(et.hour, et.minute))
        for i, cb in enumerate(self.weekday_checks):
            cb.setChecked(i in s.work_weekdays)
        self.auto_overtime.setChecked(s.auto_overtime)
        self.auto_cap.setValue(s.auto_overtime_cap_hours)
        self.coef_weekday.setValue(s.coef_weekday)
        self.coef_late.setValue(s.coef_late_night)
        self.coef_rest.setValue(s.coef_rest_day)
        self.coef_rest_late.setValue(s.coef_rest_late_night)
        self.holiday_list.clear()
        self.holiday_list.addItems(sorted(s.holidays))
        self.font_size.setValue(s.font_size)
        self.panel_width.setValue(s.panel_width)
        self.opacity.setValue(round(s.opacity * 100))
        self.today_decimals.setValue(s.today_decimals)
        self.total_decimals.setValue(s.total_decimals)
        self.show_month.setChecked(s.show_month_total)
        self.show_year.setChecked(s.show_year_total)
        self.autostart.setChecked(s.autostart)
        self._current.amount_color = s.amount_color
        self._current.clock_color = s.clock_color
        self._update_color_buttons()

    def collect(self) -> Settings:
        s = self._current.copy()
        s.annual_income = float(self.income.value())
        s.currency_symbol = self.currency.text()
        s.days_in_year = self.days_in_year.value()
        s.rest_days = self.rest_days.value()
        s.annual_leave_days = self.leave_days.value()
        s.take_home_enabled = self.take_home.isChecked()
        s.take_home_ratio = self.take_home_ratio.value() / 100.0
        s.start_time = self.start_time.time().toString("HH:mm")
        s.end_time = self.end_time.time().toString("HH:mm")
        s.work_weekdays = [i for i, cb in enumerate(self.weekday_checks) if cb.isChecked()]
        s.auto_overtime = self.auto_overtime.isChecked()
        s.auto_overtime_cap_hours = self.auto_cap.value()
        s.coef_weekday = self.coef_weekday.value()
        s.coef_late_night = self.coef_late.value()
        s.coef_rest_day = self.coef_rest.value()
        s.coef_rest_late_night = self.coef_rest_late.value()
        s.holidays = [self.holiday_list.item(i).text() for i in range(self.holiday_list.count())]
        s.font_size = self.font_size.value()
        s.panel_width = self.panel_width.value()
        s.opacity = self.opacity.value() / 100.0
        s.today_decimals = self.today_decimals.value()
        s.total_decimals = self.total_decimals.value()
        s.show_month_total = self.show_month.isChecked()
        s.show_year_total = self.show_year.isChecked()
        s.autostart = self.autostart.isChecked()
        return s.normalized()

    # -- reactions ------------------------------------------------------------

    def _changed(self, *_args) -> None:
        if self._loading:
            return
        self._current = self.collect()
        self._update_summary()
        self.preview.emit(self._current.copy())

    def _update_summary(self) -> None:
        s = self._current if self._loading else self.collect()
        rules = s.pay_rules()
        sym = s.currency_symbol
        text = (
            f"年工作日 {rules.working_days} 天 × 每天 {rules.daily_hours:g} 小时\n"
            f"日薪 {fmt_money(sym, rules.daily, 2)} · 时薪 {fmt_money(sym, rules.hourly, 2)}"
            f" · 秒薪 {fmt_money(sym, rules.per_second, 4)}"
        )
        if self.rest_days.value() + self.leave_days.value() >= self.days_in_year.value():
            text += f"\n⚠ 休息日 + 年假过多，按年工作日 {rules.working_days} 天计算"
        if s.take_home_enabled:
            text += f"\n（面板金额按到手比例 {s.take_home_ratio:.0%} 显示）"
        self.summary.setText(text)
        if rules.daily_hours > 0:
            self.hours_label.setText(f"每天 {rules.daily_hours:g} 小时（不扣午休）")
        else:
            self.hours_label.setText("⚠ 下班时间必须晚于上班时间（不支持跨夜班）")
        self.opacity_label.setText(f"{self.opacity.value()}%")

    def _update_color_buttons(self) -> None:
        for btn, color in (
            (self.amount_color_btn, self._current.amount_color),
            (self.clock_color_btn, self._current.clock_color),
        ):
            q = QColor(color)
            fg = "#000000" if q.lightness() > 128 else "#FFFFFF"
            btn.setText(q.name().upper())
            btn.setStyleSheet(f"background-color: {q.name()}; color: {fg}; padding: 4px 12px;")

    def _pick_color(self, attr: str) -> None:
        before = getattr(self._current, attr)
        dlg = QColorDialog(QColor(before), self)
        dlg.setWindowTitle("选择颜色")

        def live(color: QColor) -> None:
            if color.isValid():
                setattr(self._current, attr, color.name())
                self._update_color_buttons()
                self._changed()

        dlg.currentColorChanged.connect(live)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            live(dlg.selectedColor())
        else:
            setattr(self._current, attr, before)
            self._update_color_buttons()
            self._changed()

    def _set_holidays(self, days: list[str]) -> None:
        self.holiday_list.clear()
        self.holiday_list.addItems(sorted(set(days)))
        self._changed()

    def _holidays(self) -> list[str]:
        return [self.holiday_list.item(i).text() for i in range(self.holiday_list.count())]

    def _add_holiday(self) -> None:
        d = self.holiday_date.date()
        self._set_holidays([*self._holidays(), date(d.year(), d.month(), d.day()).isoformat()])

    def _remove_holidays(self) -> None:
        selected = {item.text() for item in self.holiday_list.selectedItems()}
        self._set_holidays([h for h in self._holidays() if h not in selected])

    def _clear_holidays(self) -> None:
        self._set_holidays([])

    def _import_holidays(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "导入假日 JSON", "", "JSON (*.json)")
        if not path:
            return
        self._import_holiday_file(Path(path), quiet=False)

    def import_example_holidays(self, *, quiet: bool = False) -> int:
        """Merge the bundled Japanese holiday list (2026–2027)."""
        return self._import_holiday_file(resource_path(HOLIDAYS_EXAMPLE), quiet=quiet)

    def _import_holiday_file(self, path: Path, *, quiet: bool) -> int:
        try:
            data = read_json(path)
        except (OSError, ValueError) as exc:
            if not quiet:
                QMessageBox.warning(self, "导入失败", f"无法读取该文件：\n{exc}")
            return 0
        days = parse_holiday_file(data)
        if not days:
            if not quiet:
                QMessageBox.warning(self, "导入失败", "文件中没有找到有效日期（格式 YYYY-MM-DD）。")
            return 0
        self._set_holidays([*self._holidays(), *days])
        if not quiet:
            QMessageBox.information(self, "导入完成", f"已导入 {len(days)} 个日期。")
        return len(days)

    def _on_button(self, button: QAbstractButton) -> None:
        role = self.buttons.buttonRole(button)
        if role == QDialogButtonBox.ButtonRole.ApplyRole:
            self._apply()
        elif role == QDialogButtonBox.ButtonRole.AcceptRole:
            self._apply()
            self.accept()
        elif role == QDialogButtonBox.ButtonRole.RejectRole:
            self.reject()

    def _apply(self) -> None:
        self._current = self.collect()
        self._original = self._current.copy()
        self.applied.emit(self._current.copy())

    def reject(self) -> None:
        # revert the live preview to the last saved settings
        self.preview.emit(self._original.copy())
        super().reject()
