"""Pointer feedback when crossing a resize grip, toolbar surface and controls."""
import pytest
from PyQt5.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt5.QtGui import QMouseEvent
from PyQt5.QtWidgets import QApplication

from ui.region_frame import RegionFrame


@pytest.fixture
def frame(request):
    app = QApplication.instance() or QApplication([])
    window = RegionFrame()
    window.setAttribute(Qt.WA_DontShowOnScreen)
    window.resize(request.param, 180)
    window.show()
    app.processEvents()
    yield window
    window.close()


def _send_mouse(widget, kind, pos, button=Qt.NoButton, buttons=Qt.NoButton):
    window_pos = widget.mapTo(widget.window(), pos)
    event = QMouseEvent(kind, QPointF(pos), QPointF(window_pos),
                        QPointF(widget.mapToGlobal(pos)), button, buttons, Qt.NoModifier)
    QApplication.sendEvent(widget, event)


def _move_to(frame, pos):
    widget = frame.childAt(pos) or frame
    # Qt only delivers buttonless moves to widgets with mouse tracking. In
    # particular a child label may inherit a stale cursor from a resize grip.
    if widget.hasMouseTracking():
        _send_mouse(widget, QEvent.MouseMove, widget.mapFrom(frame, pos))
    return widget


@pytest.mark.parametrize("frame", [850, 86], indirect=True)
def test_toolbar_has_move_cursor_after_crossing_resize_grip(frame):
    width = frame.width()
    top = frame._toolbar.height()
    for pos, expected in (
        (QPoint(1, top + 1), Qt.SizeFDiagCursor),
        (QPoint(width - 1, top + 1), Qt.SizeBDiagCursor),
        (QPoint(1, top + 16), Qt.SizeHorCursor),
        (QPoint(width // 2, top + 1), Qt.SizeVerCursor),
    ):
        assert _move_to(frame, pos).cursor().shape() == expected

    frame.set_ocr_loading()
    QApplication.processEvents()
    if frame._wide_tools.isVisible():
        labels = (frame._drag_dots, frame._size_label, frame._ocr_label)
        for label in labels:
            assert label.isVisible()
            assert _move_to(frame, label.mapTo(frame, label.rect().center())).cursor().shape() == Qt.OpenHandCursor
        buttons = (frame._btn_start, frame._btn_history, frame._btn_close)
        surface = frame._wide_tools
    else:
        buttons = (frame._compact_primary, frame._more)
        surface = frame._compact_tools
        assert frame._more.menu().cursor().shape() == Qt.ArrowCursor
    for control in buttons:
        assert _move_to(frame, control.mapTo(frame, control.rect().center())).cursor().shape() == Qt.PointingHandCursor

    # Layout margins contain no buttons or labels. These are drag targets too.
    surface_pos = surface.mapTo(frame, QPoint(1, 1))
    target = _move_to(frame, surface_pos)
    assert target is surface and target.cursor().shape() == Qt.OpenHandCursor


@pytest.mark.parametrize(("frame", "origin"), [
    (850, "toolbar"), (86, "toolbar"), (850, "dimensions"), (850, "statistics"),
], indirect=["frame"])
def test_toolbar_drag_moves_without_resizing_and_releases_hand(frame, origin, monkeypatch):
    if origin == "toolbar":
        surface = frame._wide_tools if frame._wide_tools.isVisible() else frame._compact_tools
        local = QPoint(1, 1)
    else:
        # The same rich-text label shows the capture dimensions when idle and
        # live counters while translating. Deliver movement to the label itself:
        # QLabel can consume it even when the original press reached the frame.
        frame.set_translating(origin == "statistics")
        QApplication.processEvents()
        surface = frame._size_label
        assert surface.isVisible()
        local = surface.rect().center()
        assert frame.childAt(surface.mapTo(frame, local)) is surface
    before = frame._native_window_rect()
    moves = []
    monkeypatch.setattr(frame, "_set_native_geometry", moves.append)
    _move_to(frame, QPoint(1, frame._toolbar.height() + 16))
    _send_mouse(surface, QEvent.MouseButtonPress, local, Qt.LeftButton, Qt.LeftButton)
    assert surface.cursor().shape() == Qt.ClosedHandCursor
    _send_mouse(surface, QEvent.MouseMove, local + QPoint(10, 0), buttons=Qt.LeftButton)
    assert moves and moves[-1].size() == before.size()
    assert moves[-1].x() - before.x() == pytest.approx(10 * frame.devicePixelRatioF(), abs=1)
    assert surface.cursor().shape() == Qt.ClosedHandCursor
    _send_mouse(surface, QEvent.MouseButtonRelease, local, Qt.LeftButton)
    assert surface.cursor().shape() == Qt.OpenHandCursor
