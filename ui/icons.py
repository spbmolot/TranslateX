"""
Векторные иконки для тулбара рамки и трея.

Рисуются через QPainter на прозрачном QPixmap — выглядят одинаково на всех
машинах (в отличие от emoji ▶ ⏹ ⚙ 🕑, которые ОС рендерит по-разному и часто
цветными). Все иконки монохромные: цвет задаётся параметром `color`.

API: каждая функция возвращает готовый QIcon нужного размера.
"""
from __future__ import annotations

from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import (
    QBrush,
    QColor,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)

_SIZE = 64  # логический размер канвы; Qt сам масштабирует под кнопку


def _canvas(color: str):
    """Создаёт прозрачный pixmap + painter с антиалиасингом. Возвращает (pixmap, painter, QColor)."""
    pm = QPixmap(_SIZE, _SIZE)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    return pm, p, QColor(color)


def _finish(pm, p) -> QIcon:
    p.end()
    return QIcon(pm)


def play(color: str = "#ffffff") -> QIcon:
    pm, p, c = _canvas(color)
    path = QPainterPath()
    path.moveTo(20, 14)
    path.lineTo(50, 32)
    path.lineTo(20, 50)
    path.closeSubpath()
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(c))
    p.drawPath(path)
    return _finish(pm, p)


def stop(color: str = "#ffffff") -> QIcon:
    pm, p, c = _canvas(color)
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(c))
    p.drawRoundedRect(QRectF(18, 18, 28, 28), 5, 5)
    return _finish(pm, p)


def pause(color: str = "#ffffff") -> QIcon:
    pm, p, c = _canvas(color)
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(c))
    p.drawRoundedRect(QRectF(20, 16, 9, 32), 3, 3)
    p.drawRoundedRect(QRectF(35, 16, 9, 32), 3, 3)
    return _finish(pm, p)


def select(color: str = "#ffffff") -> QIcon:
    """Рамка выбора области: пунктирный прямоугольник с уголками."""
    pm, p, c = _canvas(color)
    pen = QPen(c, 4)
    pen.setStyle(Qt.DashLine)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawRect(QRectF(16, 16, 32, 32))
    # Угловые «крестики» сплошной линией для акцента
    solid = QPen(c, 5)
    solid.setCapStyle(Qt.RoundCap)
    p.setPen(solid)
    for cx, cy in ((16, 16), (48, 16), (16, 48), (48, 48)):
        p.drawPoint(QPointF(cx, cy))
    return _finish(pm, p)


def clock(color: str = "#ffffff") -> QIcon:
    """История: циферблат со стрелками."""
    pm, p, c = _canvas(color)
    pen = QPen(c, 4)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(QRectF(14, 14, 36, 36))
    # Стрелки из центра
    cx, cy = 32, 32
    p.drawLine(QPointF(cx, cy), QPointF(cx, cy - 12))      # минутная вверх
    p.drawLine(QPointF(cx, cy), QPointF(cx + 9, cy + 3))   # часовая вправо
    return _finish(pm, p)


def gear(color: str = "#ffffff") -> QIcon:
    """Настройки: шестерёнка (8 зубцов) с отверстием."""
    pm, p, c = _canvas(color)
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(c))
    cx, cy = 32.0, 32.0
    # Зубцы — 8 скруглённых прямоугольников по кругу
    p.save()
    p.translate(cx, cy)
    for _ in range(8):
        p.drawRoundedRect(QRectF(-4.5, -27, 9, 12), 2, 2)
        p.rotate(45)
    p.restore()
    # Тело шестерёнки
    p.drawEllipse(QRectF(14, 14, 36, 36))
    # Отверстие — вырезаем прозрачностью
    p.setCompositionMode(QPainter.CompositionMode_Clear)
    p.setBrush(QBrush(QColor(0, 0, 0, 255)))
    p.drawEllipse(QRectF(25, 25, 14, 14))
    p.setCompositionMode(QPainter.CompositionMode_SourceOver)
    return _finish(pm, p)


def close(color: str = "#ffffff") -> QIcon:
    pm, p, c = _canvas(color)
    pen = QPen(c, 6)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.drawLine(QPointF(20, 20), QPointF(44, 44))
    p.drawLine(QPointF(44, 20), QPointF(20, 44))
    return _finish(pm, p)


def app_icon(active: bool = False) -> QIcon:
    """
    Дефолтная иконка приложения/трея: скруглённый квадрат с буквой «T»
    и акцентной точкой. Заметно приятнее залитого серого квадрата.
    """
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    bg = QColor("#4fc3f7") if active else QColor("#37474f")
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(bg))
    p.drawRoundedRect(QRectF(4, 4, 56, 56), 14, 14)
    # Буква T
    pen = QPen(QColor("#ffffff"), 6)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.drawLine(QPointF(20, 24), QPointF(44, 24))   # перекладина
    p.drawLine(QPointF(32, 24), QPointF(32, 46))   # ножка
    # Акцентная точка-«перевод»
    p.setPen(Qt.NoPen)
    accent = QColor("#ffffff") if active else QColor("#4fc3f7")
    p.setBrush(QBrush(accent))
    p.drawEllipse(QRectF(40, 38, 10, 10))
    p.end()
    return QIcon(pm)
