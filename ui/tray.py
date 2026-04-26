"""
Иконка в системном трее.
Меню управления на русском языке.
"""
import logging
from pathlib import Path

from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtGui import QIcon, QPixmap, QColor, QPainter
from PyQt5.QtWidgets import QAction, QMenu, QSystemTrayIcon

from core.__version__ import __version__

logger = logging.getLogger(__name__)


def _make_default_icon(active: bool = False) -> QIcon:
    pixmap = QPixmap(32, 32)
    color = QColor("#4fc3f7") if active else QColor("#546e7a")
    pixmap.fill(color)
    painter = QPainter(pixmap)
    painter.setPen(QColor("#ffffff"))
    painter.drawText(pixmap.rect(), 0x84, "TX")
    painter.end()
    return QIcon(pixmap)


class SystemTray(QObject):
    select_region_requested = pyqtSignal()
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
            "Ctrl+Shift+P — начать/остановить перевод",
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
