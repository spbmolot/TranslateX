"""
RegionFrame — Bandicam-style capture frame.
Persistent translucent border that stays on screen.
User can drag to move, resize via edges/corners, and control via toolbar.
"""
import logging
import math

from PyQt5.QtCore import QPoint, QRect, QRectF, QSize, Qt, QTimer, pyqtSignal, pyqtSlot
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from ui import icons
from ui import theme

logger = logging.getLogger(__name__)

_BORDER = 3       # border width
_TOOLBAR_H = 32   # toolbar height
_MIN_SIZE = 80    # minimum region dimension
_GRIP = 8         # edge grip zone for resize
_ICON_SZ = 16     # размер иконок на кнопках тулбара


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
        # PERF-003: OCR грузится лениво при первом Старте, поэтому индикатор
        # выключен до сигнала AppController.ocr_loading → set_ocr_loading().
        self._ocr_loading = False
        self._paused = False       # для пульсации/тоста

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

        # Тост по центру рамки (#10): «⏸ Пауза» / «▶ Работает» на ~1.1с.
        self._toast = QLabel("", self)
        self._toast.setAlignment(Qt.AlignCenter)
        self._toast.setVisible(False)
        self._toast.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._toast.setStyleSheet(
            "QLabel { color: #ffffff; background: rgba(20,20,30,200);"
            " border-radius: 8px; padding: 8px 18px; font-size: 15px; font-weight: 600; }"
        )
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.setInterval(1100)
        self._toast_timer.timeout.connect(self._toast.hide)

        # Спиннер загрузки OCR (#11): фаза анимации точек. Запускается
        # только из set_ocr_loading() — при lazy-load OCR ещё не грузится.
        self._spinner_phase = 0
        self._spinner_timer = QTimer(self)
        self._spinner_timer.setInterval(250)
        self._spinner_timer.timeout.connect(self._on_spinner_tick)

    # ── Стили (зависят от темы) ────────────────────────────────────────
    @staticmethod
    def _toolbar_qss() -> str:
        c = theme.frame_colors()
        return f"""
            QWidget#tbRoot {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {c['toolbar_top']}, stop:1 {c['toolbar_bottom']});
                border-top-left-radius: 5px;
                border-top-right-radius: 5px;
            }}
        """

    _LABEL_BTN_QSS = """
        QPushButton {
            color: white; border: 1px solid #555; border-radius: 3px;
            padding: 2px 10px; font-size: 12px; min-width: 64px;
        }
        QPushButton:hover { border-color: #4fc3f7; background: rgba(79,195,247,40); }
        QPushButton:pressed { background: rgba(79,195,247,70); }
    """

    _ICON_BTN_QSS = """
        QPushButton {
            color: white; background: transparent;
            border: 1px solid #555; border-radius: 3px;
            padding: 2px 6px; min-width: 28px;
        }
        QPushButton:hover { border-color: #4fc3f7; background: rgba(79,195,247,45); }
        QPushButton:pressed { background: rgba(79,195,247,80); }
    """

    def _build_toolbar(self) -> None:
        self._toolbar = QWidget(self)
        self._toolbar.setObjectName("tbRoot")
        self._toolbar.setFixedHeight(_TOOLBAR_H)
        self._toolbar.setStyleSheet(self._toolbar_qss())

        layout = QHBoxLayout(self._toolbar)
        layout.setContentsMargins(8, 2, 8, 2)
        layout.setSpacing(6)

        # Drag-handle точки (#12) — подсказка, что рамку можно таскать.
        self._drag_dots = QLabel("⠿", self._toolbar)
        self._drag_dots.setStyleSheet("color: #6c7a90; font-size: 16px; background: transparent;")
        self._drag_dots.setToolTip("Потяните, чтобы переместить рамку")
        layout.addWidget(self._drag_dots)

        # Size / статистика (рендерится rich-text «пилюлями» в _update_size_label)
        self._size_label = QLabel("", self._toolbar)
        self._size_label.setTextFormat(Qt.RichText)
        self._size_label.setStyleSheet("background: transparent;")
        layout.addWidget(self._size_label)

        # Индикатор загрузки OCR (#11) — скрыт, пока загрузка не началась
        self._ocr_label = QLabel("загрузка OCR", self._toolbar)
        self._ocr_label.setStyleSheet(
            "color: #ffcc66; font-size: 11px; background: transparent;"
        )
        self._ocr_label.setVisible(False)
        layout.addWidget(self._ocr_label)

        layout.addStretch()

        self._btn_start = QPushButton(" Старт", self._toolbar)
        self._btn_start.setStyleSheet(self._LABEL_BTN_QSS + "QPushButton { background: #2e7d32; }")
        self._btn_start.setIcon(icons.play())
        self._btn_start.setIconSize(QSize(_ICON_SZ, _ICON_SZ))
        self._btn_start.setCursor(Qt.PointingHandCursor)
        self._btn_start.clicked.connect(self._on_start)
        layout.addWidget(self._btn_start)

        self._btn_stop = QPushButton(" Стоп", self._toolbar)
        self._btn_stop.setStyleSheet(self._LABEL_BTN_QSS + "QPushButton { background: #c62828; }")
        self._btn_stop.setIcon(icons.stop())
        self._btn_stop.setIconSize(QSize(_ICON_SZ, _ICON_SZ))
        self._btn_stop.setCursor(Qt.PointingHandCursor)
        self._btn_stop.clicked.connect(self._on_stop)
        self._btn_stop.setVisible(False)
        layout.addWidget(self._btn_stop)

        # Pause/Resume — видим только когда идёт перевод. Toggle button.
        self._btn_pause = QPushButton(self._toolbar)
        self._btn_pause.setStyleSheet(
            self._ICON_BTN_QSS + "QPushButton { background: #455a64; }"
        )
        self._btn_pause.setIcon(icons.pause())
        self._btn_pause.setIconSize(QSize(_ICON_SZ, _ICON_SZ))
        self._btn_pause.setToolTip("Пауза/возобновить (Ctrl+Shift+Space)")
        self._btn_pause.setCursor(Qt.PointingHandCursor)
        self._btn_pause.clicked.connect(self.pause_toggle_requested)
        self._btn_pause.setVisible(False)
        layout.addWidget(self._btn_pause)

        self._btn_select = QPushButton(self._toolbar)
        self._btn_select.setToolTip("Выбрать новую область экрана")
        self._btn_select.setStyleSheet(self._ICON_BTN_QSS)
        self._btn_select.setIcon(icons.select())
        self._btn_select.setIconSize(QSize(_ICON_SZ, _ICON_SZ))
        self._btn_select.setCursor(Qt.PointingHandCursor)
        self._btn_select.clicked.connect(self.select_region_requested)
        layout.addWidget(self._btn_select)

        self._btn_history = QPushButton(self._toolbar)
        self._btn_history.setToolTip("История переводов")
        self._btn_history.setStyleSheet(self._ICON_BTN_QSS)
        self._btn_history.setIcon(icons.clock())
        self._btn_history.setIconSize(QSize(_ICON_SZ, _ICON_SZ))
        self._btn_history.setCursor(Qt.PointingHandCursor)
        self._btn_history.clicked.connect(self.history_requested)
        layout.addWidget(self._btn_history)

        self._btn_settings = QPushButton(self._toolbar)
        self._btn_settings.setToolTip("Настройки")
        self._btn_settings.setStyleSheet(self._ICON_BTN_QSS)
        self._btn_settings.setIcon(icons.gear())
        self._btn_settings.setIconSize(QSize(_ICON_SZ, _ICON_SZ))
        self._btn_settings.setCursor(Qt.PointingHandCursor)
        self._btn_settings.clicked.connect(self.settings_requested)
        layout.addWidget(self._btn_settings)

        self._btn_close = QPushButton(self._toolbar)
        self._btn_close.setToolTip("Закрыть рамку")
        self._btn_close.setStyleSheet(
            "QPushButton { background: transparent; border: none; min-width: 26px; }"
            "QPushButton:hover { background: rgba(255,82,82,60); border-radius: 3px; }"
        )
        self._btn_close.setIcon(icons.close())
        self._btn_close.setIconSize(QSize(_ICON_SZ, _ICON_SZ))
        self._btn_close.setCursor(Qt.PointingHandCursor)
        self._btn_close.clicked.connect(self._on_close)
        layout.addWidget(self._btn_close)

    def refresh_theme(self) -> None:
        """Перекрасить тулбар под текущую тему (вызывается при смене темы)."""
        self._toolbar.setStyleSheet(self._toolbar_qss())
        self._update_size_label()
        self.update()

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
        # Рамка не должна попадать в захват: скруглённые углы бордюра
        # залезают на пару пикселей внутрь области OCR. Best-effort —
        # на старых Windows API вернёт False, это не критично.
        if not getattr(self, "_capture_excluded", False):
            try:
                from utils import win32_utils
                self._capture_excluded = win32_utils.set_exclude_from_capture(
                    win32_utils.get_hwnd(self))
            except Exception:
                self._capture_excluded = False

    def set_translating(self, active: bool) -> None:
        self._translating = active
        self._btn_start.setVisible(not active)
        self._btn_stop.setVisible(active)
        self._btn_pause.setVisible(active)
        if active:
            self._pulse_timer.start()
            self._btn_stop.setText(" Работает")
            self._show_toast("Работает")
        else:
            self._pulse_timer.stop()
            self._pulse_phase = 0
            self._btn_stop.setText(" Стоп")
            # Сбросить вид pause при остановке (на случай если был paused)
            self._paused = False
            self._btn_pause.setIcon(icons.pause())
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
        self._paused = paused
        if paused:
            self._btn_pause.setIcon(icons.play())
            self._btn_pause.setToolTip("Возобновить (Ctrl+Shift+Space)")
            self._pulse_timer.stop()
            self._show_toast("Пауза")
        else:
            self._btn_pause.setIcon(icons.pause())
            self._btn_pause.setToolTip("Пауза (Ctrl+Shift+Space)")
            if self._translating:
                self._pulse_timer.start()
                self._show_toast("Работает")
        self.update()

    # ── Индикатор загрузки OCR (#11) ───────────────────────────────────────
    def set_ocr_loading(self) -> None:
        self._ocr_loading = True
        self._ocr_label.setVisible(True)
        if not self._spinner_timer.isActive():
            self._spinner_timer.start()

    def set_ocr_ready(self) -> None:
        self._ocr_loading = False
        self._spinner_timer.stop()
        self._ocr_label.setVisible(False)

    @pyqtSlot()
    def _on_spinner_tick(self) -> None:
        if not self._ocr_loading:
            return
        self._spinner_phase = (self._spinner_phase + 1) % 4
        dots = "." * (self._spinner_phase + 1)
        self._ocr_label.setText(f"загрузка OCR{dots}")

    # ── Тост по центру рамки (#10) ─────────────────────────────────────────
    def show_toast(self, text: str) -> None:
        """Публичный API: показать тост по центру рамки (например «Скопировано»)."""
        self._show_toast(text)

    def _show_toast(self, text: str) -> None:
        icon = {"Пауза": "⏸", "Работает": "▶", "Скопировано": "📋"}.get(text, "")
        self._toast.setText(f"{icon}  {text}".strip())
        self._toast.adjustSize()
        self._reposition_toast()
        self._toast.setVisible(True)
        self._toast.raise_()
        self._toast_timer.start()

    def _reposition_toast(self) -> None:
        tw, th = self._toast.width(), self._toast.height()
        x = (self.width() - tw) // 2
        y = _TOOLBAR_H + (self.height() - _TOOLBAR_H - th) // 2
        self._toast.move(max(0, x), max(_TOOLBAR_H, y))

    @pyqtSlot()
    def _on_pulse_tick(self) -> None:
        self._pulse_phase = (self._pulse_phase + 1) % 20
        self.update()

    # ── API для AppController: обновление статистики ───────────────────────

    @pyqtSlot(int)
    def on_capture_done(self, ocr_blocks: int) -> None:
        self._captures += 1
        self._ocr_blocks_last = ocr_blocks
        self._update_size_label()

    @pyqtSlot(int)
    def on_translation_done(self, translated_blocks: int) -> None:
        self._translations += 1
        self._update_size_label()

    # ── Events ──────────────────────────────────────────────────────

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._toolbar.setGeometry(0, 0, self.width(), _TOOLBAR_H)
        if self._toast.isVisible():
            self._reposition_toast()
        self._update_size_label()

    @staticmethod
    def _pill(text: str, color: str) -> str:
        """Возвращает HTML-«пилюлю» (закруглённый фон) для rich-text QLabel."""
        return (
            f"<span style='background:{color}; color:#ffffff;"
            f" padding:1px 6px; border-radius:7px; font-size:11px;'>{text}</span>"
        )

    def _update_size_label(self) -> None:
        cr = self.capture_rect()
        accent = theme.frame_colors()["sublabel"]
        size_html = (
            f"<span style='color:{accent}; font-size:11px;'>"
            f"{cr.width()} × {cr.height()}</span>"
        )
        if self._translating:
            pills = "&nbsp;".join((
                self._pill(f"кадры {self._captures}", "#37474f"),
                self._pill(f"блоки {self._ocr_blocks_last}", "#3949ab"),
                self._pill(f"перевод {self._translations}", "#2e7d32"),
            ))
            text = f"{size_html}&nbsp;&nbsp;{pills}"
        else:
            text = size_html
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

        colors = theme.frame_colors()
        # Анимация (пульсация бордюра + REC-точка) только когда реально переводим
        # и не на паузе. Считаем фазу один раз и переиспользуем.
        animating = self._translating and not self._paused
        pulse = 0.5 + 0.5 * math.sin((self._pulse_phase / 20.0) * 2 * math.pi) if animating else 0.0

        # Тёплый красный когда перевод работает, акцент темы в покое
        if self._translating:
            color = QColor(colors["active_border"])
        else:
            color = QColor(colors["idle_border"])

        if animating:
            bw = _BORDER + pulse * 2.0           # 3 → 5 px (#2 «дышащая» рамка)
            border_alpha = int(190 + pulse * 65)  # 190 → 255
        else:
            bw = float(_BORDER)
            border_alpha = 230 if self._translating else 150

        # Скруглённый бордюр (#1)
        pen = QPen(QColor(color.red(), color.green(), color.blue(), border_alpha))
        pen.setWidthF(bw)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        inset = bw / 2.0
        rect = QRectF(
            inset,
            _TOOLBAR_H,
            self.width() - bw,
            self.height() - _TOOLBAR_H - inset,
        )
        painter.drawRoundedRect(rect, 6, 6)

        # Пульсирующий REC-индикатор в правом углу тулбара
        if animating:
            radius = 5 + pulse * 3                # 5 → 8 px
            alpha = int(180 + pulse * 75)         # 180 → 255
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(255, 23, 68, alpha))
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
