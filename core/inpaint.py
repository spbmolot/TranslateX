"""
«Стирание» оригинального текста под переводом (стиль Google Lens).

Идея: по захваченному кадру для каждого текстового блока сэмплируем цвет
фона — медиану пикселей в узкой рамке ВОКРУГ bbox (сам bbox не берём, там
буквы). Overlay заливает bbox этим цветом непрозрачно и рисует перевод
контрастным (чёрным/белым по luminance). Оригинальный текст полностью
скрыт — никакого «двойного текста».

Чистые numpy-функции, без Qt.
"""
import logging
from typing import List, Optional, Tuple

import numpy as np

from core.ocr import TextBlock
from utils.geometry import easyocr_bbox_to_rect

logger = logging.getLogger(__name__)

# Толщина рамки вокруг bbox, из которой сэмплируем фон (px).
_BAND = 3


def sample_bg_color(image: np.ndarray,
                    rect: Tuple[int, int, int, int]) -> Optional[Tuple[int, int, int]]:
    """
    Медианный цвет фона вокруг rect=(x, y, w, h) на RGB-кадре.
    Возвращает (r, g, b) или None, если сэмплировать не из чего.

    Медиана (а не среднее) устойчива к попаданию в рамку кусочков соседних
    букв/иконок: половина «чистых» пикселей фона перевешивает выбросы.
    """
    ih, iw = image.shape[:2]
    x, y, w, h = rect
    x0 = max(0, x - _BAND)
    y0 = max(0, y - _BAND)
    x1 = min(iw, x + w + _BAND)
    y1 = min(ih, y + h + _BAND)
    if x1 <= x0 or y1 <= y0:
        return None

    ix0, iy0 = max(0, x), max(0, y)
    ix1, iy1 = min(iw, x + w), min(ih, y + h)

    strips = []
    if iy0 > y0:
        strips.append(image[y0:iy0, x0:x1])          # верхняя полоса
    if y1 > iy1:
        strips.append(image[iy1:y1, x0:x1])          # нижняя полоса
    if ix0 > x0:
        strips.append(image[iy0:iy1, x0:ix0])        # левая полоса
    if x1 > ix1:
        strips.append(image[iy0:iy1, ix1:x1])        # правая полоса

    if strips:
        pixels = np.concatenate([s.reshape(-1, s.shape[-1]) for s in strips
                                 if s.size > 0])
    else:
        # bbox упирается во все края кадра — берём медиану всего кадра
        pixels = image.reshape(-1, image.shape[-1])
    if pixels.size == 0:
        return None

    med = np.median(pixels[:, :3], axis=0)
    return int(med[0]), int(med[1]), int(med[2])


def apply_bg_colors(image: Optional[np.ndarray], blocks: List[TextBlock]) -> None:
    """Проставляет block.bg_rgb каждому блоку по кадру. Ошибки не фатальны:
    блок без bg_rgb overlay нарисует в fallback-стиле (обводка)."""
    if image is None or image.ndim != 3:
        return
    for b in blocks:
        try:
            b.bg_rgb = sample_bg_color(image, easyocr_bbox_to_rect(b.bbox))
        except Exception as e:
            logger.debug(f"bg sample failed for block: {e}")
            b.bg_rgb = None
