import pytest
from PyQt5.QtCore import QRect

from utils.dpi_geometry import logical_to_native, native_to_logical


class Screen:
    def __init__(self, scale):
        self.scale = scale

    def geometry(self):
        return QRect(-1920, -400, 1280, 720)

    def devicePixelRatio(self):
        return self.scale


@pytest.mark.parametrize("scale", [1, 1.25, 1.5, 1.75, 2])
def test_scale_around_secondary_monitor_origin(scale):
    screen = Screen(scale)
    rect = QRect(-1840, -360, 160, 80)
    native = QRect(-1920 + int(80 * scale), -400 + int(40 * scale),
                   int(160 * scale), int(80 * scale))
    assert logical_to_native(rect, screen) == native
    assert native_to_logical(native, screen) == rect


def test_fractional_pixels_use_same_edge_for_adjacent_areas():
    screen = Screen(1.25)
    left = logical_to_native(QRect(-1920, -400, 2, 5), screen)
    right = logical_to_native(QRect(-1918, -400, 3, 5), screen)
    assert left.width() == 3  # 2.5 native pixels rounded with Qt's qRound.
    assert left.x() + left.width() == right.x()
