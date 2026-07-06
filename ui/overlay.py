"""
TranslationOverlay — прозрачный, click-through, always-on-top слой.
Рисует переведённый текст поверх захваченной области.
Поддерживает авто-масштабирование шрифта под DPI Windows.
"""
import logging
from typing import List

from PyQt5.QtCore import QEasingCurve, QPropertyAnimation, QRect, Qt, pyqtSlot
from PyQt5.QtGui import QColor, QFont, QPainter, QPen, QBrush, QFontMetrics
from PyQt5.QtWidgets import QWidget

from core.translator import TranslatedBlock
from utils import geometry
from utils import win32_utils

logger = logging.getLogger(__name__)

_PADDING = 5  # px around text


class TranslationOverlay(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._blocks: List[TranslatedBlock] = []
        self._region: QRect = QRect()
        self._opacity = 0.85
        self._font_size = 13
        self._auto_font = True
        self._font_color = QColor("#ffffff")
        self._bg_color = QColor("#1a1a2e")
        self._style = "inpaint"
        self._visible = False
        # WDA_EXCLUDEFROMCAPTURE: окно исключено из захвата экрана — overlay
        # не нужно прятать перед каждым кадром (убирает мерцание ~1.5с).
        # Проверено эмпирически с mss/BitBlt (scripts/check_capture_exclusion.py).
        self._exclude_from_capture = True   # желаемый режим (из конфига)
        self._capture_excluded = False      # фактически применено к HWND
        # PERF-005: сигнатура последних отрисованных блоков — если контент
        # не изменился, repaint пропускается (pipeline ре-эмитит кэшированные
        # блоки каждый тик на статичном экране).
        self._last_blocks_sig = None

        self.setWindowFlags(
            Qt.FramelessWindowHint |
            Qt.WindowStaysOnTopHint |
            Qt.Tool |
            Qt.WindowTransparentForInput
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setWindowOpacity(1.0)

        # Плавное появление (#5): анимация windowOpacity 0→1 при первом show.
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(160)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.setEasingCurve(QEasingCurve.OutCubic)

    def _apply_click_through(self) -> None:
        try:
            hwnd = win32_utils.get_hwnd(self)
            win32_utils.set_click_through(hwnd)
        except Exception as e:
            logger.warning(f"Не удалось включить click-through: {e}")
        self._apply_capture_exclusion()

    def _apply_capture_exclusion(self) -> None:
        """Применяет/снимает исключение окна из захвата по _exclude_from_capture.
        При успехе hide_overlay_for_capture/restore_after_capture становятся
        no-op — overlay не мерцает. При неуспехе (старая Windows, нет DWM)
        остаётся прежний hide/show fallback."""
        try:
            hwnd = win32_utils.get_hwnd(self)
            if self._exclude_from_capture and not self._capture_excluded:
                self._capture_excluded = win32_utils.set_exclude_from_capture(hwnd)
                if self._capture_excluded:
                    logger.info("Overlay исключён из захвата экрана (без мерцания)")
                else:
                    logger.info(
                        "WDA_EXCLUDEFROMCAPTURE недоступен — fallback hide/show"
                    )
            elif not self._exclude_from_capture and self._capture_excluded:
                win32_utils.unset_exclude_from_capture(hwnd)
                self._capture_excluded = False
        except Exception as e:
            logger.warning(f"Не удалось применить capture-exclusion: {e}")

    def show_overlay(self) -> None:
        was_hidden = not self.isVisible()
        self._visible = True
        self.show()
        self._apply_click_through()
        # Fade-in только когда окно реально появляется с нуля (а не при
        # per-frame restore_after_capture, иначе моргало бы каждый кадр).
        if was_hidden:
            try:
                self._fade.stop()
                self._fade.start()
            except Exception:
                self.setWindowOpacity(1.0)
        # Подписываемся на смену экрана ОДИН раз (флаг-guard), чтобы избежать
        # дублирующих connect'ов при повторных show_overlay. Без декоратора
        # @pyqtSlot — сигнал screenChanged(QScreen*) несовместим с pyqtSlot(object)
        # и вызывает Qt fail-fast (0xc0000409) при первом эмите.
        if not getattr(self, "_screen_sig_connected", False):
            handle = self.windowHandle()
            if handle is not None:
                try:
                    handle.screenChanged.connect(self._on_screen_changed)
                    self._screen_sig_connected = True
                except Exception as e:
                    logger.warning(f"Не удалось подписаться на screenChanged: {e}")

    def _on_screen_changed(self, screen) -> None:
        """Overlay переехал на другой монитор — переустанавливаем
        click-through (HWND-флаги могут потеряться) и перерисовываем.
        Обёрнут в try/except — любая ошибка тут НЕ должна валить приложение."""
        try:
            try:
                name = screen.name() if screen is not None else "?"
            except Exception:
                name = "?"
            logger.debug(f"Overlay screen changed → '{name}', re-applying click-through")
            self._apply_click_through()
            self.update()
        except Exception as e:
            logger.exception(f"_on_screen_changed failed: {e}")

    def hide_overlay(self) -> None:
        self._visible = False
        self.hide()

    @pyqtSlot()
    def hide_overlay_for_capture(self) -> None:
        """Временно скрыть overlay перед захватом экрана, чтобы он не попал в кадр.
        Если окно исключено из захвата (WDA_EXCLUDEFROMCAPTURE) — прятать
        не нужно, no-op (перевод не мерцает)."""
        if self._capture_excluded:
            return
        if self._visible:
            self.hide()

    @pyqtSlot()
    def restore_after_capture(self) -> None:
        """Вернуть overlay после захвата."""
        if self._capture_excluded:
            return
        if self._visible and self._blocks:
            self.show()
            self._apply_click_through()

    @staticmethod
    def _blocks_signature(blocks: List[TranslatedBlock], region: QRect):
        return (
            (region.x(), region.y(), region.width(), region.height()),
            tuple(
                (
                    b.translated,
                    tuple(tuple(p) for p in b.bbox),
                    getattr(b, "bg_rgb", None),
                )
                for b in blocks
            ),
        )

    def update_blocks(self, blocks: List[TranslatedBlock], region: QRect) -> None:
        sig = self._blocks_signature(blocks, region)
        unchanged = sig == self._last_blocks_sig
        self._last_blocks_sig = sig
        self._blocks = blocks
        self._region = region
        self.setGeometry(region)
        if not blocks:
            # Нет блоков для отображения (например, всё уже на target-языке) —
            # прячем overlay, чтобы не было пустого окна и старых надписей.
            self.hide_overlay()
            return
        if not self._visible:
            self.show_overlay()
            self.update()
            return
        # PERF-005: контент и геометрия не изменились, окно уже видно —
        # repaint не нужен (экономит font-fitting и растеризацию каждый тик).
        if not unchanged:
            self.update()

    def apply_config(self, opacity: float, font_size: int,
                     font_color: str, bg_color: str,
                     auto_font: bool = True,
                     style: str = "inpaint",
                     exclude_from_capture: bool = True) -> None:
        self._opacity = opacity
        self._font_size = font_size
        self._auto_font = auto_font
        self._font_color = QColor(font_color)
        self._bg_color = QColor(bg_color)
        # 'inpaint' — залить bbox сэмплированным цветом фона и рисовать
        # контрастный текст (стиль Google Lens, оригинал полностью скрыт);
        # 'outline' — прежний стиль: текст с обводкой поверх оригинала.
        self._style = style
        self._exclude_from_capture = exclude_from_capture
        if self.isVisible():
            self._apply_capture_exclusion()
        self.update()

    def _effective_font_size(self) -> int:
        """Верхняя планка размера шрифта. DPI не домножаем — Qt сам рендерит pt
        с учётом экрана; второе домножение делало текст визуально крупнее оригинала."""
        return max(6, int(self._font_size))

    def _fit_font_size(self, text: str, max_w: int, max_h: int,
                       min_pt: int = 6, max_pt: int = 48) -> QFont:
        """
        Подбирает максимальный размер шрифта (бинарным поиском),
        при котором текст с word-wrap влезает в (max_w, max_h).
        Это ключевой UX-фикс: перевод накладывается ровно поверх оригинала
        и читается с той же визуальной плотностью.
        """
        if max_w <= 0 or max_h <= 0:
            return QFont("Segoe UI", min_pt)
        lo, hi = min_pt, max_pt
        best = min_pt
        while lo <= hi:
            mid = (lo + hi) // 2
            font = QFont("Segoe UI", mid)
            font.setWeight(QFont.Medium)
            fm = QFontMetrics(font)
            br = fm.boundingRect(
                0, 0, max_w, 10000,
                Qt.TextWordWrap | Qt.AlignLeft,
                text,
            )
            if br.width() <= max_w and br.height() <= max_h:
                best = mid
                lo = mid + 1
            else:
                hi = mid - 1
        font = QFont("Segoe UI", best)
        font.setWeight(QFont.Medium)
        return font

    def paintEvent(self, event) -> None:
        if not self._blocks:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.TextAntialiasing)

        # Если пользователь явно выставил opacity=0 — не рисуем фон вообще.
        # Иначе лёгкая подложка (<=opacity), чтобы текст читался на любом фоне.
        has_bg = self._opacity > 0.01
        if has_bg:
            bg = QColor(self._bg_color)
            bg.setAlphaF(self._opacity)

        user_cap_pt = self._effective_font_size()

        for block in self._blocks:
            bx, by, bw, bh = geometry.easyocr_bbox_to_rect(block.bbox)
            bx, by, bw, bh = geometry.clamp_rect(
                bx, by, bw, bh, self.width(), self.height()
            )
            if bw <= 4 or bh <= 4:
                continue

            text = (block.translated or "").strip()
            if not text:
                continue

            # Используем всю высоту bbox целиком (без padding), чтобы шрифт
            # перевода был такого же визуального размера, как оригинал.
            # _PADDING оставим только по горизонтали — текст пусть дышит по бокам.
            inner_w = max(8, bw - _PADDING * 2)
            inner_h = max(8, bh)

            # Высота bbox в пикселях → pt при 96 DPI: pt ≈ px * 72/96 = px * 0.75.
            # Берём чуть меньше (0.78) чтобы хвосты букв (g, y, р) не обрезались.
            # Верхняя граница пользовательской настройки ИГНОРИРУЕТСЯ — она раньше
            # зажимала шрифт на 13pt даже если оригинал был 20pt.
            height_cap_pt = max(8, int(bh * 0.78))
            # Но пользовательская настройка может увеличить лимит, если она больше bbox-пропорции
            max_pt = max(height_cap_pt, user_cap_pt)
            font = self._fit_font_size(text, inner_w, inner_h, min_pt=7, max_pt=max_pt)
            painter.setFont(font)

            text_rect_x = bx + _PADDING
            text_rect_y = by
            text_flags = Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignVCenter

            inpaint_rgb = getattr(block, "bg_rgb", None)
            if self._style == "inpaint" and inpaint_rgb is not None:
                # Inpaint-стиль (Google Lens): bbox непрозрачно заливается
                # сэмплированным цветом фона — оригинальный текст полностью
                # скрыт, перевод рисуется контрастным цветом. Никакого
                # «двойного текста».
                r, g, b = inpaint_rgb
                painter.setBrush(QBrush(QColor(r, g, b)))
                painter.setPen(Qt.NoPen)
                painter.drawRoundedRect(bx, by, bw, bh, 2, 2)
                lum = 0.299 * r + 0.587 * g + 0.114 * b
                text_color = QColor(20, 20, 20) if lum >= 140 else QColor(255, 255, 255)
                painter.setPen(QPen(text_color))
                painter.drawText(
                    text_rect_x, text_rect_y, inner_w, inner_h, text_flags, text,
                )
                continue

            # Fallback-стиль 'outline' (или блок без bg_rgb):
            # полупрозрачная подложка (если включена настройками)
            if has_bg:
                painter.setBrush(QBrush(bg))
                painter.setPen(Qt.NoPen)
                painter.drawRoundedRect(bx, by, bw, bh, 3, 3)

            # Тёмная обводка текста (stroke) — даёт читаемость без "синего фона".
            # Рисуем текст 4 раза с offset ±1, затем основной цвет.
            outline_color = QColor(0, 0, 0, 220)
            painter.setPen(QPen(outline_color))
            for ox, oy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                painter.drawText(
                    text_rect_x + ox, text_rect_y + oy,
                    inner_w, inner_h, text_flags, text,
                )

            # Основной текст
            painter.setPen(QPen(self._font_color))
            painter.drawText(
                text_rect_x, text_rect_y, inner_w, inner_h, text_flags, text,
            )

        painter.end()
