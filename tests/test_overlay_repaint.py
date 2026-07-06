"""
PERF-005: overlay не должен перерисовываться, когда pipeline ре-эмитит
идентичные блоки (каждый тик на статичном экране).
"""
import sys
from unittest.mock import MagicMock

from PyQt5.QtCore import QRect
from PyQt5.QtWidgets import QApplication

_app = QApplication.instance() or QApplication(sys.argv[:1])

from core.translator import TranslatedBlock  # noqa: E402
from ui.overlay import TranslationOverlay    # noqa: E402


def _blocks():
    return [TranslatedBlock(
        original="Hello", translated="Привет",
        bbox=[[10, 10], [100, 10], [100, 30], [10, 30]],
        confidence=0.9, bg_rgb=(200, 200, 200),
    )]


def _overlay_with_spies():
    ov = TranslationOverlay()
    ov.update = MagicMock()
    ov.show_overlay = MagicMock(side_effect=lambda: setattr(ov, "_visible", True))
    ov.hide_overlay = MagicMock(side_effect=lambda: setattr(ov, "_visible", False))
    return ov


def test_identical_blocks_skip_repaint():
    ov = _overlay_with_spies()
    region = QRect(0, 0, 200, 100)

    ov.update_blocks(_blocks(), region)   # первый показ → update
    first_calls = ov.update.call_count
    assert first_calls >= 1

    ov.update_blocks(_blocks(), region)   # тот же контент → repaint пропущен
    assert ov.update.call_count == first_calls


def test_changed_text_triggers_repaint():
    ov = _overlay_with_spies()
    region = QRect(0, 0, 200, 100)
    ov.update_blocks(_blocks(), region)
    before = ov.update.call_count

    changed = _blocks()
    changed[0].translated = "Мир"
    ov.update_blocks(changed, region)
    assert ov.update.call_count == before + 1


def test_changed_region_triggers_repaint():
    ov = _overlay_with_spies()
    ov.update_blocks(_blocks(), QRect(0, 0, 200, 100))
    before = ov.update.call_count
    ov.update_blocks(_blocks(), QRect(50, 50, 200, 100))
    assert ov.update.call_count == before + 1


def test_empty_blocks_hide_overlay():
    ov = _overlay_with_spies()
    ov.update_blocks(_blocks(), QRect(0, 0, 200, 100))
    ov.update_blocks([], QRect(0, 0, 200, 100))
    ov.hide_overlay.assert_called()
