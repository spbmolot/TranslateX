"""
Интеграционный тест inpaint-отрисовки TranslationOverlay: рендерим виджет
в QImage (без показа окна) и проверяем реальные пиксели — заливка bbox
сэмплированным цветом фона и контрастный текст поверх.
"""
import sys

import pytest
from PyQt5.QtCore import QRect
from PyQt5.QtWidgets import QApplication

# Единственный QApplication на процесс тестов
_app = QApplication.instance() or QApplication(sys.argv[:1])

from core.translator import TranslatedBlock  # noqa: E402
from ui.overlay import TranslationOverlay    # noqa: E402


def _make_overlay(style: str, opaque: bool = True) -> TranslationOverlay:
    # Продовое соответствие: inpaint → opaque+mask, outline → translucent
    ov = TranslationOverlay(opaque_mode=opaque)
    ov.resize(400, 200)
    ov.apply_config(
        opacity=0.0, font_size=13, font_color="#ffffff",
        bg_color="#1a1a2e", auto_font=True, style=style,
    )
    return ov


def _block(bg_rgb):
    return TranslatedBlock(
        original="Hello world sample",
        translated="Привет мир пример",
        bbox=[[20, 20], [380, 20], [380, 60], [20, 60]],
        confidence=0.9,
        bg_rgb=bg_rgb,
    )


def _render(ov) -> "QImage":
    img = ov.grab().toImage()
    assert not img.isNull()
    return img


def _color_at(img, x, y):
    # QWidget geometry is in DIPs; QImage addresses the backing store's pixels.
    scale = img.devicePixelRatio()
    return img.pixelColor(round(x * scale), round(y * scale))


def test_inpaint_fills_bbox_with_sampled_color():
    ov = _make_overlay("inpaint")
    ov._blocks = [_block((200, 30, 30))]  # тёмно-красный фон
    ov._region = QRect(0, 0, 400, 200)
    img = _render(ov)
    # Угол bbox (не текст): должен быть залит красным
    c = _color_at(img, 374, 24)
    assert abs(c.red() - 200) <= 40 and c.green() <= 80 and c.blue() <= 80, \
        f"ожидалась красная заливка, получено RGB({c.red()},{c.green()},{c.blue()})"


def test_inpaint_draws_contrast_text():
    """На тёмном фоне текст должен рисоваться белым — ищем светлые пиксели."""
    if _app.platformName() == "offscreen":
        # offscreen-платформа Qt на Windows не рендерит глифы вообще
        # (заливки рисуются, текст — нет). Тест осмыслен только на
        # реальном дисплее: standalone-прогон этого файла.
        pytest.skip("offscreen QPA не рендерит текст")
    ov = _make_overlay("inpaint")
    ov._blocks = [_block((30, 30, 30))]
    ov._region = QRect(0, 0, 400, 200)
    img = _render(ov)
    light = 0
    for x in range(25, 375, 3):
        for y in range(22, 58, 2):
            c = _color_at(img, x, y)
            if c.red() > 200 and c.green() > 200 and c.blue() > 200:
                light += 1
    assert light > 10, "белые пиксели текста не найдены внутри bbox"


def test_block_without_bg_translucent_falls_back_to_outline():
    """Translucent-режим: блок без bg_rgb не заливается (текст с обводкой)."""
    ov = _make_overlay("inpaint", opaque=False)
    ov._blocks = [_block(None)]
    ov._region = QRect(0, 0, 400, 200)
    img = _render(ov)
    # Угол bbox — прозрачный (нет заливки при opacity=0.0)
    c = _color_at(img, 374, 24)
    assert c.alpha() < 40, f"ожидалась прозрачность, alpha={c.alpha()}"


def test_block_without_bg_opaque_draws_solid_plate():
    """Opaque-режим: окно не умеет полупрозрачность — блок без bg_rgb
    получает плотную плашку цветом bg_color (#1a1a2e)."""
    ov = _make_overlay("inpaint", opaque=True)
    ov._blocks = [_block(None)]
    ov._region = QRect(0, 0, 400, 200)
    img = _render(ov)
    c = _color_at(img, 374, 24)
    assert c.alpha() > 200
    assert c.red() < 60 and c.blue() < 80, \
        f"ожидалась тёмная плашка, RGB({c.red()},{c.green()},{c.blue()})"


def test_outline_style_ignores_bg_rgb():
    ov = _make_overlay("outline", opaque=False)
    ov._blocks = [_block((200, 30, 30))]
    ov._region = QRect(0, 0, 400, 200)
    img = _render(ov)
    c = _color_at(img, 374, 24)
    # outline + opacity 0 → без красной заливки
    assert not (abs(c.red() - 200) <= 40 and c.green() <= 80), \
        "outline-режим не должен рисовать inpaint-заливку"


def test_opaque_mode_sets_window_mask():
    """Opaque-режим: форма окна = объединение bbox'ов (вне маски окно
    не существует — контент виден, клики проходят)."""
    ov = _make_overlay("inpaint", opaque=True)
    ov.update_blocks([_block((10, 10, 10))], QRect(0, 0, 400, 200))
    mask = ov.mask()
    assert not mask.isEmpty()
    br = mask.boundingRect()
    assert (br.x(), br.y(), br.width(), br.height()) == (20, 20, 360, 40)
    ov.hide_overlay()


def test_overlay_aligns_to_native_capture_on_windows():
    if _app.platformName() != "windows":
        pytest.skip("requires a Windows HWND")
    from utils.win32_utils import window_client_rect

    ov = _make_overlay("inpaint")
    # The capture frame and overlay may have different logical sizes when
    # their primary monitors differ. The physical capture is authoritative.
    native = QRect(30, 40, 600, 300)
    ov.update_blocks([_block((150, 30, 30))], QRect(10, 20, 400, 200),
                     _app.primaryScreen(), native)
    _app.processEvents()
    try:
        assert window_client_rect(int(ov.winId())) == native.getRect()
        ratio = ov.devicePixelRatioF()
        mask = ov.mask().boundingRect()
        actual = tuple(round(v * ratio) for v in mask.getRect())
        assert actual == pytest.approx((30, 30, 540, 60), abs=2)
        img = _render(ov)
        assert img.pixelColor(558, 36).red() >= 130
    finally:
        ov.hide_overlay()
