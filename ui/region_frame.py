"""
RegionFrame — Bandicam-style capture frame.
Persistent translucent border that stays on screen.
User can drag to move, resize via edges/corners, and control via toolbar.
"""
import logging

from PyQt5.QtCore import QPoint, QRect, QSize, Qt, QTimer, pyqtSignal, pyqtSlot
from PyQt5.QtGui import QColor, QCursor, QFont, QPainter, QPen
from PyQt5.QtWidgets import (
    QHBoxLayout, QPushButton, QSizeGrip, QWidget, QApplication,
    QLabel,
)

logger = logging.getLogger(__name__)

_BORDER = 3       # border width
_TOOLBAR_H = 32   # toolbar height
_MIN_SIZE = 80    # minimum region dimension
_GRIP = 8         # edge grip zone for resize


class RegionFrame(QWidget):
    """
    Always-on-top frame that marks the capture area.
    Shows a toolbar with Start/Stop/Close buttons on top.
    Can be moved by dragging the toolbar and resized by dragging edges/corners.
    """
    region_changed = pyqtSignal(QRect)  # emitted when region moves/resizes
    start_requested = pyqtSignal()
    stop_requested = pyqtSignal()
    close_requested = pyqtSignal()
    select_region_requested = pyqtSignal()
    settings_requested = pyqtSignal()
    history_requested = pyqtSignal()
    pause_toggle_requested = pyqtSignal()  # явный pause/resume пользователем
    # Пауза перевода: эмитится при начале drag/resize, resume — через 1.2с после mouseRelease
    translation_pause_requested = pyqtSignal()
    translation_resume_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_pos = None
        self._resizing = False
        self._resize_edge = None   # 'left','right','top','bottom','tl','tr','bl','br'
        self._translating = False  # True when actively capturing/translating
        # Счётчики активности для тулбара
        self._captures = 0
        self._ocr_blocks_last = 0
        self._translations = 0
        self._pulse_phase = 0

        self.setWindowFlags(
            Qt.FramelessWindowHint |
            Qt.WindowStaysOnTopHint |
            Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMinimumSize(_MIN_SIZE + _BORDER * 2, _MIN_SIZE + _TOOLBAR_H + _BORDER)
        self.setMouseTracking(True)

        self._build_toolbar()

        # Таймер анимации индикатора "запись" — тикает когда перевод активен
        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(120)
        self._pulse_timer.timeout.connect(self._on_pulse_tick)

        # Debounce-таймер: после release мыши ждём 1.2с прежде чем возобновлять перевод
        # (чтобы пользователь успел отпустить и кадр стабилизировался).
        self._resume_timer = QTimer(self)
        self._resume_timer.setSingleShot(True)
        self._resume_timer.setInterval(1200)
        self._resume_timer.timeout.connect(self._emit_resume)
        self._paused_by_drag = False

    def _build_toolbar(self) -> None:
        self._toolbar = QWidget(self)
        self._toolbar.setFixedHeight(_TOOLBAR_H)
        self._toolbar.setStyleSheet("""
            QWidget {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #1a1a2e, stop:1 #16213e);
                border-top-left-radius: 5px;
                border-top-right-radius: 5px;
            }
        """)

        layout = QHBoxLayout(self._toolbar)
        layout.setContentsMargins(8, 2, 8, 2)
        layout.setSpacing(6)

        # Size label
        self._size_label = QLabel("", self._toolbar)
        self._size_label.setStyleSheet("color: #8899aa; font-size: 11px;")
        layout.addWidget(self._size_label)

        layout.addStretch()

        btn_style = """
            QPushButton {
                color: white;
                border: 1px solid #555;
                border-radius: 3px;
                padding: 2px 10px;
                font-size: 12px;
                min-width: 60px;
            }
            QPushButton:hover { border-color: #4fc3f7; }
        """

        self._btn_start = QPushButton("▶ Старт", self._toolbar)
        self._btn_start.setStyleSheet(btn_style + "QPushButton { background: #2e7d32; }")
        self._btn_start.setCursor(Qt.PointingHandCursor)
        self._btn_start.clicked.connect(self._on_start)
        layout.addWidget(self._btn_start)

        self._btn_stop = QPushButton("⏹ Стоп", self._toolbar)
        self._btn_stop.setStyleSheet(btn_style + "QPushButton { background: #c62828; }")
        self._btn_stop.setCursor(Qt.PointingHandCursor)
        self._btn_stop.clicked.connect(self._on_stop)
        self._btn_stop.setVisible(False)
        layout.addWidget(self._btn_stop)

        # Pause/Resume — видим только когда идёт перевод. Toggle button.
        self._btn_pause = QPushButton("⏸", self._toolbar)
        self._btn_pause.setStyleSheet(
            btn_style + "QPushButton { background: #455a64; min-width: 32px; }"
        )
        self._btn_pause.setToolTip("Пауза/возобновить (Ctrl+Shift+Space)")
        self._btn_pause.setCursor(Qt.PointingHandCursor)
        self._btn_pause.clicked.connect(self.pause_toggle_requested)
        self._btn_pause.setVisible(False)
        layout.addWidget(self._btn_pause)

        icon_btn_style = """
            QPushButton {
                color: white; background: transparent;
                border: 1px solid #555; border-radius: 3px;
                padding: 2px 6px; font-size: 13px; min-width: 26px;
            }
            QPushButton:hover { border-color: #4fc3f7; color: #4fc3f7; }
        """

        self._btn_select = QPushButton("⛶", self._toolbar)
        self._btn_select.setToolTip("Выбрать новую область экрана")
        self._btn_select.setStyleSheet(icon_btn_style)
        self._btn_select.setCursor(Qt.PointingHandCursor)
        self._btn_select.clicked.connect(self.select_region_requested)
        layout.addWidget(self._btn_select)

        self._btn_history = QPushButton("🕑", self._toolbar)
        self._btn_history.setToolTip("История переводов")
        self._btn_history.setStyleSheet(icon_btn_style)
        self._btn_history.setCursor(Qt.PointingHandCursor)
        self._btn_history.clicked.connect(self.history_requested)
        layout.addWidget(self._btn_history)

        self._btn_settings = QPushButton("⚙", self._toolbar)
        self._btn_settings.setToolTip("Настройки")
        self._btn_settings.setStyleSheet(icon_btn_style)
        self._btn_settings.setCursor(Qt.PointingHandCursor)
        self._btn_settings.clicked.connect(self.settings_requested)
        layout.addWidget(self._btn_settings)

        self._btn_close = QPushButton("✕", self._toolbar)
        self._btn_close.setStyleSheet("""
            QPushButton {
                color: white; background: transparent;
                border: none; font-size: 16px; min-width: 24px;
            }
            QPushButton:hover { color: #ff5252; }
        """)
        self._btn_close.setCursor(Qt.PointingHandCursor)
        self._btn_close.clicked.connect(self._on_close)
        layout.addWidget(self._btn_close)

    def capture_rect(self) -> QRect:
        """Return the inner capture area in screen coordinates (excluding toolbar & border)."""
        return QRect(
            self.x() + _BORDER,
            self.y() + _TOOLBAR_H,
            self.width() - _BORDER * 2,
            self.height() - _TOOLBAR_H - _BORDER,
        )

    def show_at(self, rect: QRect) -> None:
        """Position frame around the given screen rect (from selector)."""
        self.setGeometry(
            rect.x() - _BORDER,
            rect.y() - _TOOLBAR_H,
            rect.width() + _BORDER * 2,
            rect.height() + _TOOLBAR_H + _BORDER,
        )
        self._update_size_label()
        self.show()
        self.raise_()

    def set_translating(self, active: bool) -> None:
        self._translating = active
        self._btn_start.setVisible(not active)
        self._btn_stop.setVisible(active)
        self._btn_pause.setVisible(active)
        if active:
            self._pulse_timer.start()
            self._btn_stop.setText("⏹ Работает")
        else:
            self._pulse_timer.stop()
            self._pulse_phase = 0
            self._btn_stop.setText("⏹ Стоп")
            # Сбросить вид pause при остановке (на случай если был paused)
            self.set_paused(False)
            # Сброс счётчиков
            self._captures = 0
            self._ocr_blocks_last = 0
            self._translations = 0
        self._update_size_label()
        self.update()

    @pyqtSlot(bool)
    def set_paused(self, paused: bool) -> None:
        """Слот для AppController.user_pause_changed — обновляет UI кнопки и
        приостанавливает пульсацию rec-индикатора."""
        if paused:
            self._btn_pause.setText("▶")
            self._btn_pause.setToolTip("Возобновить (Ctrl+Shift+Space)")
            self._pulse_timer.stop()
        else:
            self._btn_pause.setText("⏸")
            self._btn_pause.setToolTip("Пауза (Ctrl+Shift+Space)")
            if self._translating:
                self._pulse_timer.start()
        self.update()

    @pyqtSlot()
    def _on_pulse_tick(self) -> None:
        self._pulse_phase = (self._pulse_phase + 1) % 20
        self.update()

    # ── API для AppController: обновление статистики ───────────────────────

    def on_capture_done(self, ocr_blocks: int) -> None:
        self._captures += 1
        self._ocr_blocks_last = ocr_blocks
        self._update_size_label()

    def on_translation_done(self, translated_blocks: int) -> None:
        self._translations += 1
        self._update_size_label()

    # ── Events ──────────────────────────────────────────────────────

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._toolbar.setGeometry(0, 0, self.width(), _TOOLBAR_H)
        self._update_size_label()

    def _update_size_label(self) -> None:
        cr = self.capture_rect()
        if self._translating:
            text = (
                f"{cr.width()} × {cr.height()}  ·  "
                f"кадров: {self._captures}  ·  "
                f"блоков: {self._ocr_blocks_last}  ·  "
                f"переводов: {self._translations}"
            )
        else:
            text = f"{cr.width()} × {cr.height()}"
        self._size_label.setText(text)
        # Эмитим region_changed ТОЛЬКО при реальном изменении геометрии,
        # иначе обновление счётчиков (кадров/блоков) спамит pipeline.set_region
        # и засоряет лог сотнями одинаковых "Capture region set".
        if cr != getattr(self, "_last_emitted_rect", None):
            self._last_emitted_rect = cr
            self.region_changed.emit(cr)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Тёплый красный когда перевод работает, голубой в покое
        if self._translating:
            color = QColor("#ff5252")
            border_alpha = 220
        else:
            color = QColor("#4fc3f7")
            border_alpha = 140

        # Frame border
        pen = QPen(QColor(color.red(), color.green(), color.blue(), border_alpha), _BORDER)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(
            _BORDER // 2,
            _TOOLBAR_H,
            self.width() - _BORDER,
            self.height() - _TOOLBAR_H - _BORDER // 2,
        )

        # Пульсирующий REC-индикатор в правом углу тулбара
        if self._translating:
            # phase: 0..19, синусоидальная пульсация радиуса/альфы
            import math
            t = self._pulse_phase / 20.0
            pulse = 0.5 + 0.5 * math.sin(t * 2 * math.pi)
            radius = 5 + pulse * 3                # 5 → 8 px
            alpha = int(180 + pulse * 75)         # 180 → 255
            dot_color = QColor(255, 23, 68, alpha)
            painter.setPen(Qt.NoPen)
            painter.setBrush(dot_color)
            cx, cy = self.width() - 16, _TOOLBAR_H // 2
            painter.drawEllipse(
                int(cx - radius), int(cy - radius),
                int(radius * 2), int(radius * 2),
            )

        painter.end()

    def _edge_at(self, pos: QPoint) -> str:
        """Determine which edge/corner the cursor is near (for resize).
        Пока курсор над тулбаром — это зона перемещения, а не resize."""
        x, y = pos.x(), pos.y()
        w, h = self.width(), self.height()
        # Над тулбаром resize не активен (весь этот слой — перетаскивание)
        if y < _TOOLBAR_H:
            return ""
        on_left = x < _GRIP
        on_right = x > w - _GRIP
        on_top = _TOOLBAR_H <= y < _TOOLBAR_H + _GRIP
        on_bottom = y > h - _GRIP

        if on_top and on_left: return "tl"
        if on_top and on_right: return "tr"
        if on_bottom and on_left: return "bl"
        if on_bottom and on_right: return "br"
        if on_left: return "left"
        if on_right: return "right"
        if on_top: return "top"
        if on_bottom: return "bottom"
        return ""

    _CURSOR_MAP = {
        "left": Qt.SizeHorCursor,   "right": Qt.SizeHorCursor,
        "top": Qt.SizeVerCursor,    "bottom": Qt.SizeVerCursor,
        "tl": Qt.SizeFDiagCursor,   "br": Qt.SizeFDiagCursor,
        "tr": Qt.SizeBDiagCursor,   "bl": Qt.SizeBDiagCursor,
    }

    def mouseMoveEvent(self, event) -> None:
        if self._resizing and self._resize_edge:
            self._do_resize(event.globalPos())
            return
        if self._drag_pos is not None:
            delta = event.globalPos() - self._drag_pos
            self.move(self.pos() + delta)
            self._drag_pos = event.globalPos()
            self._update_size_label()
            return
        # Обновляем курсор в зависимости от зоны
        if event.pos().y() < _TOOLBAR_H:
            # Тулбар — зона перемещения. На кнопках Qt сам покажет PointingHandCursor.
            self.setCursor(Qt.SizeAllCursor)
            return
        edge = self._edge_at(event.pos())
        if edge:
            self.setCursor(self._CURSOR_MAP.get(edge, Qt.ArrowCursor))
        else:
            self.setCursor(Qt.ArrowCursor)

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        edge = self._edge_at(event.pos())
        started_interaction = False
        if edge:
            self._resizing = True
            self._resize_edge = edge
            self._resize_origin = event.globalPos()
            self._resize_geom = self.geometry()
            started_interaction = True
        elif event.pos().y() < _TOOLBAR_H:
            self._drag_pos = event.globalPos()
            started_interaction = True
        # Если начали двигать/ресайзить во время активного перевода — паузим pipeline.
        if started_interaction and self._translating and not self._paused_by_drag:
            self._paused_by_drag = True
            self._resume_timer.stop()
            self.translation_pause_requested.emit()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            was_interacting = self._drag_pos is not None or self._resizing
            self._drag_pos = None
            self._resizing = False
            self._resize_edge = None
            self._update_size_label()
            if was_interacting and self._paused_by_drag:
                # Перезапускаем дебаунс-таймер: возобновим перевод спустя 1.2с,
                # чтобы текст в кадре устоялся и не размазался при движении.
                self._resume_timer.start()

    def _emit_resume(self) -> None:
        if self._paused_by_drag:
            self._paused_by_drag = False
            self.translation_resume_requested.emit()

    # Наборы граней для корректного диагонального resize (баг был в `in` проверках)
    _LEFT_EDGES = frozenset({"left", "tl", "bl"})
    _RIGHT_EDGES = frozenset({"right", "tr", "br"})
    _TOP_EDGES = frozenset({"top", "tl", "tr"})
    _BOTTOM_EDGES = frozenset({"bottom", "bl", "br"})

    def _do_resize(self, gpos: QPoint) -> None:
        dx = gpos.x() - self._resize_origin.x()
        dy = gpos.y() - self._resize_origin.y()
        g = self._resize_geom
        x, y, w, h = g.x(), g.y(), g.width(), g.height()
        e = self._resize_edge
        min_w = _MIN_SIZE + _BORDER * 2
        min_h = _MIN_SIZE + _TOOLBAR_H + _BORDER

        if e in self._LEFT_EDGES:
            nw = max(w - dx, min_w)
            x = x + w - nw
            w = nw
        if e in self._RIGHT_EDGES:
            w = max(w + dx, min_w)
        if e in self._TOP_EDGES:
            nh = max(h - dy, min_h)
            y = y + h - nh
            h = nh
        if e in self._BOTTOM_EDGES:
            h = max(h + dy, min_h)

        self.setGeometry(x, y, w, h)
        self._update_size_label()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self._on_close()

    # ── Button handlers ─────────────────────────────────────────────

    def _on_start(self) -> None:
        self.set_translating(True)
        self.start_requested.emit()

    def _on_stop(self) -> None:
        self.set_translating(False)
        self.stop_requested.emit()

    def _on_close(self) -> None:
        self.set_translating(False)
        self.hide()
        self.close_requested.emit()
