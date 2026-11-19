"""
Иконка в системном трее.
Меню управления на русском языке.
"""
import logging
from pathlib import Path

from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QAction, QMenu, QSystemTrayIcon

from core.__version__ import __version__
from ui import icons

logger = logging.getLogger(__name__)


def _make_default_icon(active: bool = False) -> QIcon:
    """Скруглённая иконка с буквой «T» и акцентной точкой (приятнее
    залитого серого квадрата с «TX»)."""
    return icons.app_icon(active)


class SystemTray(QObject):
    show_frame_requested = pyqtSignal()
    pause_toggle_requested = pyqtSignal()
    copy_requested = pyqtSignal()
    select_region_requested = pyqtSignal()
    fullscreen_requested = pyqtSignal()
    toggle_requested = pyqtSignal()
    settings_requested = pyqtSignal()
    quit_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._active = False
        self._shortcut_hints = []
        self._tray = QSystemTrayIcon(parent)
        self._tray.setToolTip(f"TranslateX v{__version__} — ожидание")
        self._build_menu()
        self._update_icon()

    def _build_menu(self) -> None:
        menu = QMenu()

        show_frame = menu.addAction("Показать рамку")
        show_frame.triggered.connect(self.show_frame_requested)
        menu.setDefaultAction(show_frame)

        self._action_toggle = QAction("▶  Начать перевод", menu)
        self._action_toggle.triggered.connect(self.toggle_requested)
        menu.addAction(self._action_toggle)

        self._action_pause = menu.addAction("Пауза")
        self._action_pause.setEnabled(False)
        self._action_pause.triggered.connect(self.pause_toggle_requested)
        menu.addAction("Скопировать перевод").triggered.connect(self.copy_requested)

        action_select = QAction("🔲  Выбрать область", menu)
        action_select.triggered.connect(self.select_region_requested)
        menu.addAction(action_select)

        action_fullscreen = QAction("🖥  Перевести весь экран", menu)
        action_fullscreen.triggered.connect(self.fullscreen_requested)
        menu.addAction(action_fullscreen)

        menu.addSeparator()

        action_settings = QAction("⚙  Настройки...", menu)
        action_settings.triggered.connect(self.settings_requested)
        menu.addAction(action_settings)

        menu.addSeparator()

        action_quit = QAction("✕  Выход", menu)
        action_quit.triggered.connect(self.quit_requested)
        menu.addAction(action_quit)

        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_activated)

    def _on_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.show_frame_requested.emit()

    def set_shortcuts(self, select: str, toggle: str, fullscreen: str, copy: str) -> None:
        self._shortcut_hints = [
            f"{key} — {label}" for key, label in (
                (select, "выбрать область экрана"),
                (toggle, "начать/остановить перевод"),
                (fullscreen, "перевести весь экран"),
                (copy, "скопировать перевод"),
            ) if key
        ]

    def _update_icon(self) -> None:
        icon_path = Path(__file__).parent.parent / "assets" / "icon.png"
        if icon_path.exists():
            icon = QIcon(str(icon_path))
        else:
            icon = _make_default_icon(self._active)
        self._tray.setIcon(icon)

    def show(self) -> None:
        self._tray.show()
        self._tray.showMessage(
            "TranslateX",
            "\n".join(["Щёлкните значок, чтобы показать рамку.", *self._shortcut_hints]),
            QSystemTrayIcon.Information,
            4000,
        )

    def set_active(self, active: bool) -> None:
        self._active = active
        self._action_pause.setEnabled(active)
        if not active:
            self.set_paused(False)
        self._action_toggle.setText(
            "⏹  Остановить перевод" if active else "▶  Начать перевод"
        )
        tooltip = (
            f"TranslateX v{__version__} — переводим"
            if active else f"TranslateX v{__version__} — ожидание"
        )
        self._tray.setToolTip(tooltip)
        self._update_icon()

    def set_paused(self, paused: bool) -> None:
        self._action_pause.setText("Возобновить перевод" if paused else "Пауза")

    def set_status(self, message: str) -> None:
        self._tray.setToolTip(f"TranslateX v{__version__} — {message}")

    def set_ocr_loading(self) -> None:
        self._tray.setToolTip(f"TranslateX v{__version__} — загрузка OCR...")
        self.set_status("загрузка OCR...")

    def notify(self, message: str, warning: bool = False) -> None:
        """Всплывающее уведомление из трея (видно и без рамки — например,
        в полноэкранном режиме перевода)."""
        self._tray.showMessage(
            "TranslateX",
            message,
            QSystemTrayIcon.Warning if warning else QSystemTrayIcon.Information,
            6000,
        )
