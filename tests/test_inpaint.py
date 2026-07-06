"""Тесты сэмплирования цвета фона (core/inpaint.py)."""
import numpy as np

from core.inpaint import sample_bg_color, apply_bg_colors
from core.ocr import TextBlock


def _img(h, w, rgb):
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:, :] = rgb
    return img


def test_uniform_background():
    img = _img(100, 200, (240, 240, 240))
    assert sample_bg_color(img, (50, 40, 80, 20)) == (240, 240, 240)


def test_ignores_text_pixels_inside_bbox():
    """Внутри bbox — «текст» (чёрный), вокруг — белый фон.
    Сэмплируется только рамка вокруг → белый."""
    img = _img(100, 200, (255, 255, 255))
    img[40:60, 50:130] = (0, 0, 0)  # сам блок текста
    assert sample_bg_color(img, (50, 40, 80, 20)) == (255, 255, 255)


def test_median_resists_outliers():
    """Одиночные цветные пиксели в рамке не сбивают медиану."""
    img = _img(100, 200, (200, 200, 200))
    img[38, 50] = (255, 0, 0)  # выброс в верхней полосе
    r, g, b = sample_bg_color(img, (50, 40, 80, 20))
    assert (r, g, b) == (200, 200, 200)


def test_bbox_at_image_edge():
    """bbox прижат к краю кадра — не падаем, сэмплируем доступные полосы."""
    img = _img(50, 100, (10, 20, 30))
    assert sample_bg_color(img, (0, 0, 100, 20)) == (10, 20, 30)


def test_bbox_covers_whole_image():
    """bbox = весь кадр: полос вокруг нет → медиана всего кадра."""
    img = _img(50, 100, (99, 98, 97))
    assert sample_bg_color(img, (0, 0, 100, 50)) == (99, 98, 97)


def test_apply_bg_colors_sets_field():
    img = _img(100, 200, (5, 6, 7))
    blocks = [TextBlock("hi", [[10, 10], [50, 10], [50, 30], [10, 30]], 0.9)]
    apply_bg_colors(img, blocks)
    assert blocks[0].bg_rgb == (5, 6, 7)


def test_apply_bg_colors_none_image_is_noop():
    blocks = [TextBlock("hi", [[10, 10], [50, 10], [50, 30], [10, 30]], 0.9)]
    apply_bg_colors(None, blocks)
    assert blocks[0].bg_rgb is None
