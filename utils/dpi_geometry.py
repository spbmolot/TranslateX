"""Map Windows desktop rectangles using one Qt window's monitor and DPI.

Qt preserves Windows monitor origins in its virtual desktop, scaling offsets
from the monitor origin, including for screens left of or above the primary.
"""
from PyQt5.QtCore import QPoint, QPointF, QRect
from PyQt5.QtGui import QScreen


def _scale_from_origin(rect: QRect, origin: QPoint, factor: float) -> QRect:
    x, y = rect.x() - origin.x(), rect.y() - origin.y()
    start = (QPointF(x, y) * factor).toPoint()
    end = (QPointF(x + rect.width(), y + rect.height()) * factor).toPoint()
    return QRect(origin.x() + start.x(), origin.y() + start.y(),
                 end.x() - start.x(), end.y() - start.y())


def logical_to_native(rect: QRect, screen: QScreen) -> QRect:
    """Return the physical pixels covered by a Qt rectangle on this screen."""
    return _scale_from_origin(rect, screen.geometry().topLeft(), screen.devicePixelRatio())


def native_to_logical(rect: QRect, screen: QScreen) -> QRect:
    """Express a physical desktop rectangle in the given window's Qt coordinates."""
    return _scale_from_origin(rect, screen.geometry().topLeft(), 1 / screen.devicePixelRatio())
