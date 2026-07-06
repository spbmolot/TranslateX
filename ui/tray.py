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
    select_region_requested = pyqtSignal()
    fullscreen_requested = pyqtSignal()
    toggle_requested = pyqtSignal()
    settings_requested = pyqtSignal()
    quit_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._active = False
        self._tray = QSystemTrayIcon(parent)
        self._tray.setToolTip(f"TranslateX v{__version__} — ожидание")
        self._build_menu()
        self._update_icon()

    def _build_menu(self) -> None:
        menu = QMenu()

        self._action_toggle = QAction("▶  Начать перевод", menu)
        self._action_toggle.triggered.connect(self.toggle_requested)
        menu.addAction(self._action_toggle)

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
        if reason == QSystemTrayIcon.DoubleClick:
            self.toggle_requested.emit()

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
            "Приложение работает в трее.\n"
            "Ctrl+Shift+T — выбрать область экрана\n"
            "Ctrl+Shift+F — перевести весь экран\n"
            "Ctrl+Shift+P — начать/остановить перевод\n"
            "Ctrl+Shift+C — скопировать перевод",
            QSystemTrayIcon.Information,
            4000,
        )

    def set_active(self, active: bool) -> None:
        self._active = active
        self._action_toggle.setText(
            "⏹  Остановить перевод" if active else "▶  Начать перевод"
        )
        tooltip = (
            f"TranslateX v{__version__} — переводим"
            if active else f"TranslateX v{__version__} — ожидание"
        )
        self._tray.setToolTip(tooltip)
        self._update_icon()

    def set_status(self, message: str) -> None:
        self._tray.setToolTip(f"TranslateX v{__version__} — {message}")

    def set_ocr_loading(self) -> None:
        self._tray.setToolTip(f"TranslateX v{__version__} — загрузка OCR...")
        self.set_status("загрузка OCR...")
