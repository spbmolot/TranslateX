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


def _make_overlay(style: str) -> TranslationOverlay:
    ov = TranslationOverlay()
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


def test_inpaint_fills_bbox_with_sampled_color():
    ov = _make_overlay("inpaint")
    ov._blocks = [_block((200, 30, 30))]  # тёмно-красный фон
    ov._region = QRect(0, 0, 400, 200)
    img = _render(ov)
    # Угол bbox (не текст): должен быть залит красным
    c = img.pixelColor(374, 24)
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
            c = img.pixelColor(x, y)
            if c.red() > 200 and c.green() > 200 and c.blue() > 200:
                light += 1
    assert light > 10, "белые пиксели текста не найдены внутри bbox"


def test_block_without_bg_falls_back_to_outline():
    """Блок без bg_rgb не должен заливаться, даже в режиме inpaint."""
    ov = _make_overlay("inpaint")
    ov._blocks = [_block(None)]
    ov._region = QRect(0, 0, 400, 200)
    img = _render(ov)
    # Угол bbox — прозрачный (нет заливки при opacity=0.0)
    c = img.pixelColor(374, 24)
    assert c.alpha() < 40, f"ожидалась прозрачность, alpha={c.alpha()}"


def test_outline_style_ignores_bg_rgb():
    ov = _make_overlay("outline")
    ov._blocks = [_block((200, 30, 30))]
    ov._region = QRect(0, 0, 400, 200)
    img = _render(ov)
    c = img.pixelColor(374, 24)
    # outline + opacity 0 → без красной заливки
    assert not (abs(c.red() - 200) <= 40 and c.green() <= 80), \
        "outline-режим не должен рисовать inpaint-заливку"
