"""Mixed-scale drag selections and physical capture bounds of a single framed HWND."""
from types import SimpleNamespace
from unittest.mock import Mock

from PyQt5.QtCore import QPoint, QRect
from PyQt5.QtWidgets import QApplication

from ui.region_frame import RegionFrame
from ui.selector import RegionSelector


def test_drag_across_scaled_desktop_chooses_frame_screen():
    app = QApplication.instance() or QApplication([])
    selector = RegionSelector()
    primary = Mock()
    primary.geometry.return_value = QRect(0, 0, 1920, 1080)
    primary.devicePixelRatio.return_value = 1.0
    left = Mock()
    left.geometry.return_value = QRect(-1920, 0, 1280, 720)
    left.devicePixelRatio.return_value = 1.5
    selector._panes = [
        SimpleNamespace(target_screen=s, update=Mock(), hide=Mock(), deleteLater=Mock())
        for s in (primary, left)
    ]
    result = []
    selector.region_selected.connect(result.append)

    # Drag left from the primary into a 150% monitor. Qt logical desktop
    # coordinates have a 640-DIP hole here; the physical selection does not.
    selector.begin(QPoint(29, 299))
    selector.finish(QPoint(-300, 150))
    assert selector.selected_screen is left
    assert result == [QRect(-840, 100, 220, 100)]
    assert not selector._panes


def test_capture_bounds_use_native_window_origin_and_scaled_margins():
    # Only the toolbar and border scale; the desktop origin can lie outside
    # the owner monitor when the user drags the frame across their boundary.
    frame = Mock()
    frame._native_window_rect.return_value = QRect(-15, 100, 510, 353)
    frame.devicePixelRatioF.return_value = 1.5
    assert RegionFrame.capture_native_rect(frame) == QRect(-10, 148, 500, 300)
