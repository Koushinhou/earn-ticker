"""Shared UI helpers: fonts, clock seam, formatting and the coin icon."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QIcon, QPainter, QPen, QPixmap, QRadialGradient

FONT_FAMILIES = ["Microsoft YaHei UI", "Microsoft YaHei", "Meiryo UI", "Meiryo", "sans-serif"]


def app_font(point_size: float | None = None, pixel_size: int | None = None) -> QFont:
    font = QFont()
    font.setFamilies(FONT_FAMILIES)
    if pixel_size is not None:
        font.setPixelSize(max(1, int(pixel_size)))
    elif point_size is not None:
        font.setPointSizeF(point_size)
    return font


class Clock:
    """Source of the current time. Swap for ``FixedClock`` in screenshots/tests."""

    def now(self) -> datetime:
        return datetime.now().replace(microsecond=0)


class FixedClock(Clock):
    def __init__(self, moment: datetime) -> None:
        self.moment = moment

    def now(self) -> datetime:
        return self.moment

    def advance(self, seconds: int = 1) -> None:
        from datetime import timedelta

        self.moment += timedelta(seconds=seconds)


def fmt_money(symbol: str, amount: float, decimals: int) -> str:
    return f"{symbol}{amount:,.{decimals}f}"


def coin_pixmap(size: int) -> QPixmap:
    """Draw a simple gold coin."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    r = QRectF(0.5, 0.5, size - 1, size - 1)
    grad = QRadialGradient(QPointF(size * 0.35, size * 0.3), size * 0.8)
    grad.setColorAt(0.0, QColor("#FFF3A0"))
    grad.setColorAt(0.5, QColor("#FFD700"))
    grad.setColorAt(1.0, QColor("#C99A00"))
    p.setBrush(QBrush(grad))
    p.setPen(QPen(QColor("#9C7300"), max(1.0, size / 24)))
    p.drawEllipse(r)
    inner = r.adjusted(size * 0.14, size * 0.14, -size * 0.14, -size * 0.14)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QColor("#B8860B"), max(1.0, size / 32)))
    p.drawEllipse(inner)
    font = QFont()
    font.setFamilies(["Arial", *FONT_FAMILIES])
    font.setBold(True)
    font.setPixelSize(int(size * 0.55))
    p.setFont(font)
    p.setPen(QColor("#8B6508"))
    p.drawText(r, Qt.AlignmentFlag.AlignCenter, "¥")
    p.end()
    return pm


def coin_icon() -> QIcon:
    icon = QIcon()
    for s in (16, 20, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(coin_pixmap(s))
    return icon
