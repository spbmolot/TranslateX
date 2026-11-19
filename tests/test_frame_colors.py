"""Check contrast of the pixels Qt actually composites with the application's theme."""
from collections import Counter
from functools import lru_cache
from pathlib import Path

import pytest
from PyQt5.QtCore import QPoint, QStandardPaths, Qt
from PyQt5.QtGui import QFontDatabase, QImage
from PyQt5.QtWidgets import QApplication

from ui import theme
from ui.region_frame import RegionFrame


@pytest.fixture
def app():
    qapp = QApplication.instance() or QApplication([])
    style, palette, mode = qapp.styleSheet(), qapp.palette(), theme.current_mode()
    font_id = -1
    if not QFontDatabase().families():
        # The offscreen plugin on Windows starts with no fonts. Use the same
        # system font as desktop Windows so text contrast is actually tested.
        for directory in QStandardPaths.standardLocations(QStandardPaths.FontsLocation):
            font_id = QFontDatabase.addApplicationFont(str(Path(directory) / "segoeui.ttf"))
            if font_id >= 0:
                break
        assert font_id >= 0, "Qt has no font available to render the readouts"
    yield qapp
    if font_id >= 0:
        QFontDatabase.removeApplicationFont(font_id)
    theme.apply_theme(qapp, mode)
    qapp.setStyleSheet(style)
    qapp.setPalette(palette)


@lru_cache(maxsize=2048)
def _luminance(rgb):
    def linear(channel):
        c = (channel & 255) / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * linear(rgb >> 16) + 0.7152 * linear(rgb >> 8) + 0.0722 * linear(rgb)


def _render(frame):
    # Rendering the whole window includes any opaque background inherited by
    # the nested containers. Grabbing a button alone loses this information.
    image = QImage(frame.size() * 2, QImage.Format_ARGB32)
    image.setDevicePixelRatio(2)
    image.fill(Qt.transparent)
    frame.render(image)
    return image


def _assert_glyph_contrast(frame, image, widget, minimum):
    assert widget.isVisible(), widget
    pos = widget.mapTo(frame, QPoint())
    # Exclude the button border. On each scanline compare its glyph with the
    # dominant interior colour, so a vertical gradient doesn't count as ink.
    x, y = 2 * pos.x(), 2 * pos.y()
    n = 0
    for dy in range(6, 2 * widget.height() - 6):
        row = [image.pixel(x + dx, y + dy)
               for dx in range(6, 2 * widget.width() - 6)]
        bg = _luminance(Counter(row).most_common(1)[0][0])
        for pixel in row:
            fg = _luminance(pixel)
            if (max(bg, fg) + .05) / (min(bg, fg) + .05) >= minimum:
                n += 1
    assert n >= 8, f"{widget.toolTip() or widget.text()}: only {n} legible pixels at {minimum}:1"


@pytest.mark.parametrize("first,second", [("light", "dark"), ("dark", "light")])
def test_toolbar_icons_and_readouts_contrast_on_creation_and_theme_change(app, first, second):
    theme.apply_theme(app, first)
    frame = RegionFrame()
    frame.setAttribute(Qt.WA_DontShowOnScreen)
    frame.resize(850, 160)
    frame.show()
    try:
        for mode in (first, second):
            if mode == second:
                theme.apply_theme(app, second)
                frame.refresh_theme()
            frame.resize(850, 160)
            frame.set_translating(False)
            frame.set_ocr_loading()
            app.processEvents()
            image = _render(frame)
            for control in (frame._btn_select, frame._btn_history, frame._btn_settings, frame._btn_close):
                _assert_glyph_contrast(frame, image, control, 3)
            for label in (frame._size_label, frame._ocr_label):
                _assert_glyph_contrast(frame, image, label, 4.5)
            frame.set_ocr_ready()
            frame.set_translating(True)
            app.processEvents()
            _assert_glyph_contrast(frame, _render(frame), frame._btn_pause, 3)

            # Use an icon-only primary control at the minimum supported width.
            frame.resize(frame.minimumWidth(), 160)
            for running in (True, False):
                frame.set_translating(running)
                app.processEvents()
                image = _render(frame)
                _assert_glyph_contrast(frame, image, frame._compact_primary, 3)
                _assert_glyph_contrast(frame, image, frame._more, 3)
    finally:
        frame.close()
