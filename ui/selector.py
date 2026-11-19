"""Select a physical desktop area using one correctly scaled Qt window per monitor."""
import logging

from PyQt5.QtCore import QObject, QPoint, QRect, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QCursor, QPainter, QPen
from PyQt5.QtWidgets import QApplication, QWidget

from utils.dpi_geometry import logical_to_native, native_to_logical

logger = logging.getLogger(__name__)


class RegionSelector(QObject):
    region_selected = pyqtSignal(QRect)
    cancelled = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._panes = []
        self._start = None
        self.selection = QRect()  # Physical pixels throughout a drag across monitors.
        self.selected_screen = None
        QApplication.instance().screenRemoved.connect(self._screen_removed)

    def activate(self) -> None:
        self._dismiss()
        self.selected_screen = None
        screens = QApplication.screens()
        if not screens:
            self.cancel()
            return
        focus_screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        for screen in screens:
            pane = _SelectorPane(self, screen)
            self._panes.append(pane)
            pane.showFullScreen()
            pane.raise_()
        for pane in self._panes:
            if pane.target_screen == focus_screen:
                pane.raise_()
                pane.activateWindow()
                pane.setFocus()
                break

    def _dismiss(self) -> None:
        for pane in self._panes:
            pane.hide()
            pane.deleteLater()
        self._panes.clear()
        self._start = None
        self.selection = QRect()

    def _screen_removed(self, screen) -> None:
        if self._panes:
            self.cancel()

    def cancel(self) -> None:
        self._dismiss()
        self.cancelled.emit()

    def begin(self, point: QPoint) -> None:
        self._start = point
        self.drag(point)

    def drag(self, point: QPoint) -> None:
        if self._start is not None:
            self.selection = QRect(self._start, point).normalized()
            for pane in self._panes:
                pane.update()

    def finish(self, point: QPoint) -> None:
        if self._start is None:
            return
        self.drag(point)
        # Match the window manager's choice for a frame that crosses a boundary.
        def covered_area(pane):
            screen = pane.target_screen
            overlap = self.selection.intersected(logical_to_native(screen.geometry(), screen))
            return overlap.width() * overlap.height()
        screen = max(self._panes, key=covered_area).target_screen
        rect = native_to_logical(self.selection, screen)
        if rect.width() <= 10 or rect.height() <= 10:
            self.cancel()
            return
        self.selected_screen = screen
        self._dismiss()
        logger.info("Region selected: %s", rect)
        self.region_selected.emit(rect)


class _SelectorPane(QWidget):
    def __init__(self, selector, screen):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.selector = selector
        self.target_screen = screen
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(Qt.CrossCursor)
        self.winId()
        self.windowHandle().setScreen(screen)
        self.setGeometry(screen.geometry())

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.selector.cancel()

    def _physical_pos(self, event) -> QPoint:
        # Qt keeps delivering mouse events to the pane that received the press.
        # The local position stays in this window's DIPs, even beyond its edge.
        p = event.localPos() * self.devicePixelRatioF()
        return self.target_screen.geometry().topLeft() + p.toPoint()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.selector.begin(self._physical_pos(event))

    def mouseMoveEvent(self, event) -> None:
        self.selector.drag(self._physical_pos(event))

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.selector.finish(self._physical_pos(event))

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 120))

        selected = self.selector.selection
        if not selected.isEmpty():
            logical = native_to_logical(selected, self.target_screen)
            local_sel = logical.translated(-self.target_screen.geometry().topLeft())
            painter.setCompositionMode(QPainter.CompositionMode_Clear)
            painter.fillRect(local_sel, Qt.transparent)
            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
            painter.setPen(QPen(QColor("#4fc3f7"), 2))
            painter.drawRect(local_sel)

            label = f"{selected.width()} × {selected.height()}"
            fm = painter.fontMetrics()
            bw, bh = fm.horizontalAdvance(label) + 12, fm.height() + 6
            bx, by = local_sel.x(), local_sel.y() - bh - 2
            if by < 0:
                by = local_sel.y() + 2
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(20, 20, 30, 200))
            painter.drawRoundedRect(bx, by, bw, bh, 4, 4)
            painter.setPen(Qt.white)
            painter.drawText(bx, by, bw, bh, Qt.AlignCenter, label)

        painter.end()
