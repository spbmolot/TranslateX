"""
RegionFrame — Bandicam-style capture frame.
Persistent translucent border that stays on screen.
User can drag to move, resize via edges/corners, and control via toolbar.
"""
import logging
import math

from PyQt5.QtCore import QPoint, QRect, QRectF, QSize, Qt, QTimer, pyqtSignal, pyqtSlot
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import QApplication, QHBoxLayout, QLabel, QMenu, QPushButton, QToolButton, QWidget

from ui import icons
from ui import theme
from utils.dpi_geometry import logical_to_native, native_to_logical

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
        self._watched_screen = None
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
        self._pause_shortcut = ""

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
            QWidget#wideTools, QWidget#compactTools {{ background: transparent; }}
            QLabel#dragGrip {{ color: {c['sublabel']}; font-size: 16px; background: transparent; }}
        """ + """
            QPushButton, QToolButton {
                background: transparent; color: white; border: 1px solid #899ab4;
                border-radius: 3px; padding: 2px 6px;
            }
            QPushButton { min-width: 28px; }
            QPushButton#startButton, QPushButton#stopButton {
                min-width: 64px; padding: 2px 10px; font-size: 12px;
            }
            QPushButton#closeButton { min-width: 26px; border-color: transparent; }
            QPushButton:hover, QToolButton:hover { background: #485d7c; border-color: #b5d8ff; }
            QPushButton:pressed, QToolButton:pressed { background: #263751; }
            QPushButton#closeButton:hover { background: #ac3036; }
            QPushButton#closeButton:pressed { background: #80252a; }
            QPushButton:focus, QToolButton:focus, QPushButton#closeButton:focus { border-color: white; }
            QToolButton::menu-indicator { image: none; }
        """

    @staticmethod
    def _action_colors(normal: str, hover: str, pressed: str) -> str:
        return (
            f"QAbstractButton {{ background: {normal}; }}"
            f"QAbstractButton:hover {{ background: {hover}; }}"
            f"QAbstractButton:pressed {{ background: {pressed}; }}"
        )

    def _build_toolbar(self) -> None:
        self._toolbar = QWidget(self)
        self._toolbar.setObjectName("tbRoot")
        self._toolbar.setFixedHeight(_TOOLBAR_H)
        # Child labels and empty layout space inherit this even when the last
        # mouse move delivered to the frame was on a resize handle.
        self._toolbar.setCursor(Qt.OpenHandCursor)
        self._toolbar.setStyleSheet(self._toolbar_qss())

        toolbar_layout = QHBoxLayout(self._toolbar)
        toolbar_layout.setContentsMargins(0, 0, 0, 0)
        toolbar_layout.setSpacing(0)
        self._wide_tools = QWidget(self._toolbar)
        self._wide_tools.setObjectName("wideTools")
        toolbar_layout.addWidget(self._wide_tools)
        layout = QHBoxLayout(self._wide_tools)
        layout.setContentsMargins(8, 2, 8, 2)
        layout.setSpacing(6)

        # Drag-handle точки (#12) — подсказка, что рамку можно таскать.
        self._drag_dots = QLabel("⠿", self._toolbar)
        self._drag_dots.setObjectName("dragGrip")
        self._drag_dots.setToolTip("Потяните, чтобы переместить рамку")
        layout.addWidget(self._drag_dots)

        # Size / статистика (рендерится rich-text «пилюлями» в _update_size_label)
        self._size_label = QLabel("", self._toolbar)
        self._size_label.setTextFormat(Qt.RichText)
        self._size_label.setTextInteractionFlags(Qt.NoTextInteraction)
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
        self._btn_start.setObjectName("startButton")
        self._btn_start.setStyleSheet(self._action_colors("#2e7d32", "#276e2d", "#205524"))
        self._btn_start.setIcon(icons.play())
        self._btn_start.setIconSize(QSize(_ICON_SZ, _ICON_SZ))
        self._btn_start.setCursor(Qt.PointingHandCursor)
        self._btn_start.clicked.connect(self._on_start)
        layout.addWidget(self._btn_start)

        self._btn_stop = QPushButton(" Стоп", self._toolbar)
        self._btn_stop.setObjectName("stopButton")
        self._btn_stop.setStyleSheet(self._action_colors("#c62828", "#ad2424", "#8a2020"))
        self._btn_stop.setIcon(icons.stop())
        self._btn_stop.setIconSize(QSize(_ICON_SZ, _ICON_SZ))
        self._btn_stop.setCursor(Qt.PointingHandCursor)
        self._btn_stop.clicked.connect(self._on_stop)
        self._btn_stop.setVisible(False)
        layout.addWidget(self._btn_stop)

        # Pause/Resume — видим только когда идёт перевод. Toggle button.
        self._btn_pause = QPushButton(self._toolbar)
        self._btn_pause.setStyleSheet(self._action_colors("#455a64", "#526976", "#37474f"))
        self._btn_pause.setIcon(icons.pause())
        self._btn_pause.setIconSize(QSize(_ICON_SZ, _ICON_SZ))
        self._btn_pause.setCursor(Qt.PointingHandCursor)
        self._btn_pause.clicked.connect(self.pause_toggle_requested)
        self._btn_pause.setVisible(False)
        layout.addWidget(self._btn_pause)

        self._btn_select = QPushButton(self._toolbar)
        self._btn_select.setToolTip("Выбрать новую область экрана")
        self._btn_select.setIcon(icons.select())
        self._btn_select.setIconSize(QSize(_ICON_SZ, _ICON_SZ))
        self._btn_select.setCursor(Qt.PointingHandCursor)
        self._btn_select.clicked.connect(self.select_region_requested)
        layout.addWidget(self._btn_select)

        self._btn_history = QPushButton(self._toolbar)
        self._btn_history.setToolTip("История переводов")
        self._btn_history.setIcon(icons.clock())
        self._btn_history.setIconSize(QSize(_ICON_SZ, _ICON_SZ))
        self._btn_history.setCursor(Qt.PointingHandCursor)
        self._btn_history.clicked.connect(self.history_requested)
        layout.addWidget(self._btn_history)

        self._btn_settings = QPushButton(self._toolbar)
        self._btn_settings.setToolTip("Настройки")
        self._btn_settings.setIcon(icons.gear())
        self._btn_settings.setIconSize(QSize(_ICON_SZ, _ICON_SZ))
        self._btn_settings.setCursor(Qt.PointingHandCursor)
        self._btn_settings.clicked.connect(self.settings_requested)
        layout.addWidget(self._btn_settings)

        self._btn_close = QPushButton(self._toolbar)
        self._btn_close.setToolTip("Закрыть рамку")
        self._btn_close.setObjectName("closeButton")
        self._btn_close.setIcon(icons.close())
        self._btn_close.setIconSize(QSize(_ICON_SZ, _ICON_SZ))
        self._btn_close.setCursor(Qt.PointingHandCursor)
        self._btn_close.clicked.connect(self.close)
        layout.addWidget(self._btn_close)
        for button in (self._btn_select, self._btn_history, self._btn_settings, self._btn_close):
            button.setAccessibleName(button.toolTip())

        self._compact_tools = QWidget(self._toolbar)
        self._compact_tools.setObjectName("compactTools")
        compact = QHBoxLayout(self._compact_tools)
        compact.setContentsMargins(4, 2, 4, 2)
        compact.setSpacing(4)
        compact.addStretch()
        self._compact_primary = QToolButton(self._compact_tools)
        self._compact_primary.setFocusPolicy(Qt.StrongFocus)
        self._compact_primary.setCursor(Qt.PointingHandCursor)
        self._compact_primary.clicked.connect(self._on_compact_primary)
        compact.addWidget(self._compact_primary)
        self._more = QToolButton(self._compact_tools)
        self._more.setText("…")
        self._more.setAccessibleName("Ещё действия")
        self._more.setToolTip("Ещё действия")
        self._more.setFixedWidth(28)
        self._more.setFocusPolicy(Qt.StrongFocus)
        self._more.setCursor(Qt.PointingHandCursor)
        self._more.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(self._more)
        menu.setCursor(Qt.ArrowCursor)
        self._pause_action = menu.addAction("Пауза")
        self._pause_action.triggered.connect(self.pause_toggle_requested)
        menu.addAction("Выбрать область").triggered.connect(self.select_region_requested)
        menu.addAction("История переводов").triggered.connect(self.history_requested)
        menu.addAction("Настройки").triggered.connect(self.settings_requested)
        menu.addSeparator()
        menu.addAction("Закрыть рамку").triggered.connect(self.close)
        self._more.setMenu(menu)
        compact.addWidget(self._more)
        toolbar_layout.addWidget(self._compact_tools)
        self._compact_tools.hide()
        self._update_primary_action()
        self.set_pause_shortcut("")

    def _on_compact_primary(self) -> None:
        if self._translating:
            self._on_stop()
        else:
            self._on_start()

    def _update_primary_action(self) -> None:
        button = self._btn_stop if self._translating else self._btn_start
        action = "Остановить перевод" if self._translating else "Начать перевод"
        self._compact_primary.setText(button.text().strip())
        self._compact_primary.setIcon(button.icon())
        self._compact_primary.setStyleSheet(button.styleSheet())
        self._compact_primary.setToolTip(action)
        self._compact_primary.setAccessibleName(action)
        self._pause_action.setVisible(self._translating)

    def _fit_toolbar(self) -> None:
        # Keep the regular controls visible as long as they fit, sacrificing
        # the counters before moving those controls into the overflow menu.
        stats_width = self._size_label.sizeHint().width() + self._wide_tools.layout().spacing()
        controls_width = self._wide_tools.sizeHint().width()
        if not self._size_label.isHidden():
            controls_width -= stats_width
        self._size_label.setVisible(self.width() >= controls_width + stats_width)
        compact = self.width() < controls_width
        self._wide_tools.setVisible(not compact)
        self._compact_tools.setVisible(compact)
        if compact:
            # Even the minimum 80-DIP capture leaves room for both icons.
            style = Qt.ToolButtonTextBesideIcon if self.width() >= 130 else Qt.ToolButtonIconOnly
            self._compact_primary.setToolButtonStyle(style)

    def set_pause_shortcut(self, text: str) -> None:
        self._pause_shortcut = text
        action = "Возобновить" if self._paused else "Пауза"
        self._btn_pause.setAccessibleName(action)
        self._btn_pause.setToolTip(f"{action} ({text})" if text else action)
        self._pause_action.setText(f"{action}\t{text}" if text else action)

    def refresh_theme(self) -> None:
        """Перекрасить тулбар под текущую тему (вызывается при смене темы)."""
        self._toolbar.setStyleSheet(self._toolbar_qss())
        self._update_size_label()
        self.update()

    def capture_rect(self) -> QRect:
        """Capture area in the frame screen's logical Qt coordinates."""
        return native_to_logical(self.capture_native_rect(), self.screen())

    def _native_window_rect(self) -> QRect:
        if self.windowHandle() and QApplication.platformName() == "windows":
            from utils.win32_utils import window_client_rect
            bounds = window_client_rect(int(self.winId()))
            if bounds is not None:
                return QRect(*bounds)
        return logical_to_native(self.geometry(), self.screen())

    def capture_native_rect(self) -> QRect:
        """Physical desktop pixels inside the frame, including when it spans monitors."""
        margins = QSize(_BORDER, _TOOLBAR_H) * self.devicePixelRatioF()
        return self._native_window_rect().adjusted(
            margins.width(), margins.height(), -margins.width(), -margins.width())

    def show_at(self, rect: QRect, screen=None) -> None:
        """Position the frame around a selection expressed on its owner screen."""
        screen = screen or QApplication.screenAt(rect.center()) or self.screen()
        if self.windowHandle() is None:
            self.winId()
            self.windowHandle().screenChanged.connect(self._watch_screen)
        self.windowHandle().setScreen(screen)
        self._watch_screen(screen)
        outer = rect.adjusted(-_BORDER, -_TOOLBAR_H, _BORDER, _BORDER)
        self.setGeometry(outer)
        self.show()
        if QApplication.platformName() == "windows":
            # A logical corner can fall in a Qt virtual desktop gap. Avoid Qt
            # guessing which monitor owns that position on mixed-DPI desktops.
            from utils.win32_utils import set_frameless_rect
            margins = QSize(_BORDER, _TOOLBAR_H) * screen.devicePixelRatio()
            bounds = logical_to_native(rect, screen).adjusted(
                -margins.width(), -margins.height(), margins.width(), margins.width())
            minimum = self.minimumSize() * screen.devicePixelRatio()
            bounds.setSize(bounds.size().expandedTo(minimum))
            set_frameless_rect(int(self.winId()), *bounds.getRect())
        self.raise_()
        self._update_size_label()
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

    def _watch_screen(self, screen) -> None:
        if self._watched_screen == screen:
            return
        previous = self._watched_screen
        self._watched_screen = screen
        if previous is not None:
            try:
                previous.geometryChanged.disconnect(self._screen_geometry_changed)
                previous.logicalDotsPerInchChanged.disconnect(self._screen_geometry_changed)
            except RuntimeError:
                pass  # QScreen was destroyed during monitor removal.
        if screen is not None:
            screen.geometryChanged.connect(self._screen_geometry_changed)
            screen.logicalDotsPerInchChanged.connect(self._screen_geometry_changed)
            self._screen_geometry_changed()

    def _screen_geometry_changed(self, *_):
        # Qt finishes applying the new window geometry after screenChanged.
        QTimer.singleShot(0, self._update_size_label)

    def set_translating(self, active: bool) -> None:
        self._translating = active
        self._btn_start.setVisible(not active)
        self._btn_stop.setVisible(active)
        self._btn_pause.setVisible(active)
        if active:
            self._pulse_timer.start()
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
        self._update_primary_action()
        self.set_pause_shortcut(self._pause_shortcut)
        self._update_size_label()
        self.update()

    @pyqtSlot(bool)
    def set_paused(self, paused: bool) -> None:
        """Слот для AppController.user_pause_changed — обновляет UI кнопки и
        приостанавливает пульсацию rec-индикатора."""
        self._paused = paused
        self.set_pause_shortcut(self._pause_shortcut)
        if paused:
            self._btn_pause.setIcon(icons.play())
            self._pulse_timer.stop()
            self._show_toast("Пауза")
        else:
            self._btn_pause.setIcon(icons.pause())
            if self._translating:
                self._pulse_timer.start()
                self._show_toast("Работает")
        self.update()

    # ── Индикатор загрузки OCR (#11) ───────────────────────────────────────
    def set_ocr_loading(self) -> None:
        self._ocr_loading = True
        self._ocr_label.setVisible(True)
        self._fit_toolbar()
        if not self._spinner_timer.isActive():
            self._spinner_timer.start()

    def set_ocr_ready(self) -> None:
        self._ocr_loading = False
        self._spinner_timer.stop()
        self._ocr_label.setVisible(False)
        self._fit_toolbar()

    @pyqtSlot()
    def _on_spinner_tick(self) -> None:
        if not self._ocr_loading:
            return
        self._spinner_phase = (self._spinner_phase + 1) % 4
        dots = "." * (self._spinner_phase + 1)
        self._ocr_label.setText(f"загрузка OCR{dots}")
        self._fit_toolbar()

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

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        self._update_size_label()

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
        native = self.capture_native_rect()
        cr = native_to_logical(native, self.screen())
        accent = theme.frame_colors()["sublabel"]
        size_html = (
            f"<span style='color:{accent}; font-size:11px;'>"
            f"{native.width()} × {native.height()}</span>"
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
        self._fit_toolbar()
        # Эмитим region_changed ТОЛЬКО при реальном изменении геометрии,
        # иначе обновление счётчиков (кадров/блоков) спамит pipeline.set_region
        # и засоряет лог сотнями одинаковых "Capture region set".
        state = (cr, native, self.screen(), self.devicePixelRatioF())
        if state != getattr(self, "_last_emitted_rect", None):
            self._last_emitted_rect = state
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
        if y < _TOOLBAR_H or not self.rect().contains(pos):
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

    def _set_hover_cursor(self, pos: QPoint) -> None:
        if self._toolbar.geometry().contains(pos):
            self.setCursor(Qt.OpenHandCursor)
        else:
            self.setCursor(self._CURSOR_MAP.get(self._edge_at(pos), Qt.ArrowCursor))

    def _native_pointer(self, event) -> QPoint:
        offset = (event.localPos() * self.devicePixelRatioF()).toPoint()
        return self._native_window_rect().topLeft() + offset

    def _set_native_geometry(self, bounds: QRect) -> None:
        if QApplication.platformName() == "windows":
            from utils.win32_utils import set_frameless_rect
            if set_frameless_rect(int(self.winId()), *bounds.getRect()):
                return
        self.setGeometry(native_to_logical(bounds, self.screen()))

    def mouseMoveEvent(self, event) -> None:
        if self._resizing and self._resize_edge:
            self._do_resize(self._native_pointer(event))
            return
        if self._drag_pos is not None:
            bounds = self._native_window_rect()
            grab_offset = (self._drag_pos * self.devicePixelRatioF()).toPoint()
            bounds.moveTopLeft(self._native_pointer(event) - grab_offset)
            self._set_native_geometry(bounds)
            return
        self._set_hover_cursor(event.pos())

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        edge = self._edge_at(event.pos())
        started_interaction = False
        if edge:
            self._resizing = True
            self._resize_edge = edge
            self.setCursor(self._CURSOR_MAP[edge])
            self._resize_origin = self._native_pointer(event)
            self._resize_geom = self._native_window_rect()
            started_interaction = True
        elif self._toolbar.geometry().contains(event.pos()):
            self._drag_pos = event.localPos()
            self._toolbar.setCursor(Qt.ClosedHandCursor)
            self.setCursor(Qt.ClosedHandCursor)
            started_interaction = True
        # Если начали двигать/ресайзить во время активного перевода — паузим pipeline.
        if started_interaction and self._translating and not self._paused_by_drag:
            self._paused_by_drag = True
            self._resume_timer.stop()
            self.translation_pause_requested.emit()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            was_interacting = self._drag_pos is not None or self._resizing
            if self._drag_pos is not None:
                self._toolbar.setCursor(Qt.OpenHandCursor)
            self._drag_pos = None
            self._resizing = False
            self._resize_edge = None
            self._set_hover_cursor(event.pos())
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
        minimum = self.minimumSize() * self.devicePixelRatioF()
        min_w, min_h = minimum.width(), minimum.height()

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

        self._set_native_geometry(QRect(x, y, w, h))

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.close()
            event.accept()
            return
        super().keyPressEvent(event)

    # ── Button handlers ─────────────────────────────────────────────

    def _on_start(self) -> None:
        self.set_translating(True)
        self.start_requested.emit()

    def _on_stop(self) -> None:
        self.set_translating(False)
        self.stop_requested.emit()

    def closeEvent(self, event) -> None:
        self._resume_timer.stop()
        self._paused_by_drag = False
        self._drag_pos = None
        self._resizing = False
        self._resize_edge = None
        self._toolbar.setCursor(Qt.OpenHandCursor)
        self.unsetCursor()
        self.set_translating(False)
        self.close_requested.emit()
        super().closeEvent(event)
