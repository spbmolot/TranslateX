"""
Темы оформления (U3): auto / light / dark.

Применяется ко всему `QApplication` через `setStyleSheet(...)` + `setPalette(...)`.
Влияет ТОЛЬКО на Qt-виджеты приложения (Settings, History, диалоги).
Не влияет на:
  • overlay перевода (он сам рисует через QPainter — собственный цвет/обводка),
  • RegionFrame (translucent + кастомный paintEvent),
  • selector (кастомный fullscreen).

Mode "auto":
  Читаем `HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize\\AppsUseLightTheme`.
  0 = тёмная, 1 = светлая. Не Windows / нет ключа → fallback "light".
"""
from __future__ import annotations

import logging
import sys
from typing import Literal

from PyQt5.QtGui import QColor, QPalette
from PyQt5.QtWidgets import QApplication

logger = logging.getLogger(__name__)

ThemeMode = Literal["auto", "light", "dark"]

# ── Цветовые палитры ────────────────────────────────────────────────────────

LIGHT_QSS = """
QWidget        { background-color: #fafafa; color: #1e1e1e; }
QDialog        { background-color: #fafafa; }
QTabWidget::pane { border: 1px solid #c8c8c8; background: #ffffff; }
QTabBar::tab    { background: #eeeeee; padding: 6px 12px; border: 1px solid #c8c8c8; border-bottom: none; }
QTabBar::tab:selected { background: #ffffff; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QListWidget {
    background: #ffffff; border: 1px solid #c0c0c0; padding: 3px 5px;
    selection-background-color: #2d7be5; selection-color: #ffffff;
}
QPushButton    { background: #ffffff; border: 1px solid #b0b0b0; padding: 5px 12px; border-radius: 3px; }
QPushButton:hover    { background: #eef5ff; border-color: #2d7be5; }
QPushButton:pressed  { background: #dceaff; }
QCheckBox, QRadioButton { spacing: 6px; }
QLabel         { background: transparent; }
QToolTip       { background: #ffffe0; color: #1e1e1e; border: 1px solid #b0b0b0; padding: 3px; }
"""

DARK_QSS = """
QWidget        { background-color: #2b2b2b; color: #e0e0e0; }
QDialog        { background-color: #2b2b2b; }
QTabWidget::pane { border: 1px solid #3c3f41; background: #313335; }
QTabBar::tab    { background: #3c3f41; color: #e0e0e0; padding: 6px 12px; border: 1px solid #555; border-bottom: none; }
QTabBar::tab:selected { background: #4e5254; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QListWidget {
    background: #3c3f41; color: #e0e0e0; border: 1px solid #555; padding: 3px 5px;
    selection-background-color: #4f8cf3; selection-color: #ffffff;
}
QComboBox QAbstractItemView { background: #3c3f41; color: #e0e0e0; selection-background-color: #4f8cf3; }
QPushButton    { background: #3c3f41; color: #e0e0e0; border: 1px solid #555; padding: 5px 12px; border-radius: 3px; }
QPushButton:hover    { background: #4a525a; border-color: #4f8cf3; }
QPushButton:pressed  { background: #2d3239; }
QCheckBox, QRadioButton { spacing: 6px; color: #e0e0e0; }
QLabel         { background: transparent; color: #e0e0e0; }
QToolTip       { background: #3c3f41; color: #e0e0e0; border: 1px solid #777; padding: 3px; }
QMenu          { background: #3c3f41; color: #e0e0e0; border: 1px solid #555; }
QMenu::item:selected { background: #4f8cf3; }
QScrollBar:vertical, QScrollBar:horizontal { background: #2b2b2b; border: none; }
QScrollBar::handle { background: #555; border-radius: 4px; }
QScrollBar::handle:hover { background: #777; }
"""


def _detect_system_dark() -> bool:
    """
    Возвращает True если активна системная тёмная тема Windows.
    На не-Windows возвращает False (light по умолчанию).
    """
    if sys.platform != "win32":
        return False
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return value == 0
    except (FileNotFoundError, OSError) as e:
        logger.debug("Не удалось определить системную тему: %s", e)
        return False


def resolve_mode(mode: ThemeMode) -> Literal["light", "dark"]:
    """auto → конкретно light/dark; light/dark → как есть."""
    if mode == "auto":
        return "dark" if _detect_system_dark() else "light"
    if mode in ("light", "dark"):
        return mode
    logger.warning("Неизвестная тема %r — применяю 'light'", mode)
    return "light"


def _build_palette(dark: bool) -> QPalette:
    """Дополнительно к QSS ставим QPalette — нужно для виджетов, которые
    игнорируют stylesheet (системные диалоги, QMessageBox на некоторых стилях)."""
    p = QPalette()
    if dark:
        p.setColor(QPalette.Window,        QColor("#2b2b2b"))
        p.setColor(QPalette.WindowText,    QColor("#e0e0e0"))
        p.setColor(QPalette.Base,          QColor("#3c3f41"))
        p.setColor(QPalette.AlternateBase, QColor("#313335"))
        p.setColor(QPalette.Text,          QColor("#e0e0e0"))
        p.setColor(QPalette.Button,        QColor("#3c3f41"))
        p.setColor(QPalette.ButtonText,    QColor("#e0e0e0"))
        p.setColor(QPalette.Highlight,     QColor("#4f8cf3"))
        p.setColor(QPalette.HighlightedText, QColor("#ffffff"))
        p.setColor(QPalette.ToolTipBase,   QColor("#3c3f41"))
        p.setColor(QPalette.ToolTipText,   QColor("#e0e0e0"))
    else:
        p.setColor(QPalette.Window,        QColor("#fafafa"))
        p.setColor(QPalette.WindowText,    QColor("#1e1e1e"))
        p.setColor(QPalette.Base,          QColor("#ffffff"))
        p.setColor(QPalette.AlternateBase, QColor("#f0f0f0"))
        p.setColor(QPalette.Text,          QColor("#1e1e1e"))
        p.setColor(QPalette.Button,        QColor("#ffffff"))
        p.setColor(QPalette.ButtonText,    QColor("#1e1e1e"))
        p.setColor(QPalette.Highlight,     QColor("#2d7be5"))
        p.setColor(QPalette.HighlightedText, QColor("#ffffff"))
        p.setColor(QPalette.ToolTipBase,   QColor("#ffffe0"))
        p.setColor(QPalette.ToolTipText,   QColor("#1e1e1e"))
    return p


# ── Текущий режим + акцентные цвета для кастомных виджетов ───────────────────
# RegionFrame и overlay рисуются вручную через QPainter и НЕ управляются QSS.
# Чтобы они тоже следовали теме, держим текущий режим в модульной переменной
# и отдаём цвета через accent()/frame_colors(). apply_theme() обновляет режим.
_current_mode: Literal["light", "dark"] = "light"


def current_mode() -> Literal["light", "dark"]:
    return _current_mode


def frame_colors() -> dict:
    """
    Палитра для RegionFrame (рамка + тулбар) под текущую тему.
    Ключи: toolbar_top, toolbar_bottom, idle_border, active_border, sublabel.
    """
    if _current_mode == "dark":
        return {
            "toolbar_top": "#1a1a2e",
            "toolbar_bottom": "#16213e",
            "idle_border": "#4fc3f7",
            "active_border": "#ff5252",
            "sublabel": "#8899aa",
        }
    # light
    return {
        "toolbar_top": "#3a4a6b",
        "toolbar_bottom": "#2b3a57",
        "idle_border": "#2d7be5",
        "active_border": "#e53935",
        "sublabel": "#c5d2e8",
    }


def apply_theme(qapp: QApplication, mode: ThemeMode = "auto") -> str:
    """
    Применяет тему. Возвращает фактически применённый режим
    ("light" или "dark") — удобно для лога.
    """
    global _current_mode
    resolved = resolve_mode(mode)
    _current_mode = resolved
    qss = DARK_QSS if resolved == "dark" else LIGHT_QSS
    qapp.setStyleSheet(qss)
    qapp.setPalette(_build_palette(resolved == "dark"))
    logger.info("Тема: %s (запрошена: %s)", resolved, mode)
    return resolved
