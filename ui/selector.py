"""
RegionSelector — fullscreen drag-to-select widget.
Shown temporarily when the user triggers the "select region" hotkey.
Emits region_selected(QRect) with absolute screen coordinates on mouse release.
"""
import logging

from PyQt5.QtCore import QPoint, QRect, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QCursor, QPainter, QPen
from PyQt5.QtWidgets import QApplication, QWidget

logger = logging.getLogger(__name__)


class RegionSelector(QWidget):
    region_selected = pyqtSignal(QRect)
    cancelled = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._start: QPoint = QPoint()
        self._end: QPoint = QPoint()
        self._dragging = False

        self.setWindowFlags(
            Qt.FramelessWindowHint |
            Qt.WindowStaysOnTopHint |
            Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(QCursor(Qt.CrossCursor))
        self.setWindowOpacity(1.0)

    def activate(self) -> None:
        """Show the selector over ALL connected monitors (virtual desktop)."""
        # Виртуальный desktop = объединение всех экранов. На multi-monitor
        # primaryScreen().geometry() покрывает только один монитор —
        # selector был бы невидим на втором.
        screen = QApplication.primaryScreen()
        if screen is not None:
            virtual = screen.virtualGeometry()
        else:
            virtual = QApplication.desktop().geometry()
        # Объединяем геометрии всех экранов на случай если virtualGeometry
        # вернула non-rect union (бывает при необычных конфигурациях).
        for s in QApplication.screens():
            virtual = virtual.united(s.geometry())
        self.setGeometry(virtual)
        # showFullScreen() свернул бы окно к одному монитору — используем show().
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.hide()
            self.cancelled.emit()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._start = event.globalPos()
            self._end = event.globalPos()
            self._dragging = True
            self.update()

    def mouseMoveEvent(self, event) -> None:
        if self._dragging:
            self._end = event.globalPos()
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self._dragging:
            self._dragging = False
            self._end = event.globalPos()
            self.hide()
            rect = self._selection_rect()
            if rect.width() > 10 and rect.height() > 10:
                logger.info(f"Region selected: {rect}")
                self.region_selected.emit(rect)
            else:
                self.cancelled.emit()

    def _selection_rect(self) -> QRect:
        return QRect(self._start, self._end).normalized()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Dark overlay over entire screen
        overlay_color = QColor(0, 0, 0, 120)
        painter.fillRect(self.rect(), overlay_color)

        if self._dragging or (self._start != self._end):
            sel = self._selection_rect()
            # Convert from global coords to widget-local coords
            local_sel = QRect(
                sel.x() - self.x(),
                sel.y() - self.y(),
                sel.width(),
                sel.height(),
            )

            # Clear selection area
            painter.setCompositionMode(QPainter.CompositionMode_Clear)
            painter.fillRect(local_sel, Qt.transparent)
            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)

            # Selection border
            pen = QPen(QColor("#4fc3f7"), 2)
            painter.setPen(pen)
            painter.drawRect(local_sel)

            # Dimensions label с полупрозрачной плашкой (#4) — читается на любом фоне.
            label = f"{sel.width()} × {sel.height()}"
            fm = painter.fontMetrics()
            tw = fm.horizontalAdvance(label)
            th = fm.height()
            pad_x, pad_y = 6, 3
            # Плашку рисуем НАД рамкой; если места сверху нет — внутри сверху.
            bx = local_sel.x()
            by = local_sel.y() - th - pad_y * 2 - 2
            if by < 0:
                by = local_sel.y() + 2
            bw = tw + pad_x * 2
            bh = th + pad_y * 2
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(20, 20, 30, 200))
            painter.drawRoundedRect(bx, by, bw, bh, 4, 4)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(
                bx, by, bw, bh,
                Qt.AlignCenter, label,
            )

        painter.end()
