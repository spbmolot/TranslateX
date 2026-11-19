"""Frame and tray commands remain available when the capture area is narrow or hidden."""
from unittest.mock import patch

import pytest
from PyQt5.QtCore import QRect, Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QSystemTrayIcon

from ui.region_frame import RegionFrame
from ui.tray import SystemTray


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("width", [80, 180])
def test_small_frame_has_distinct_working_controls(qapp, width):
    frame = RegionFrame()
    requested = []
    frame.start_requested.connect(lambda: requested.append("start"))
    frame.settings_requested.connect(lambda: requested.append("settings"))
    frame.stop_requested.connect(lambda: requested.append("stop"))
    frame.show_at(QRect(100, 100, width, 100))
    qapp.processEvents()
    try:
        assert frame.capture_rect().width() == pytest.approx(width, abs=1)
        assert frame._compact_primary.isVisible() and frame._more.isVisible()
        for button in (frame._compact_primary, frame._more):
            assert frame._compact_tools.rect().contains(button.geometry())
        assert not frame._compact_primary.geometry().intersects(frame._more.geometry())
        frame._compact_primary.click()
        settings = next(a for a in frame._more.menu().actions() if a.text() == "Настройки")
        settings.trigger()
        frame._compact_primary.click()
        assert requested == ["start", "settings", "stop"]
    finally:
        frame.close()


@pytest.mark.parametrize("source", ["native", "button", "escape"])
def test_close_frame_stops_translation_once(qapp, source):
    frame = RegionFrame()
    frame.show_at(QRect(100, 100, 700, 150))
    frame.set_translating(True)
    closed = []
    frame.close_requested.connect(lambda: closed.append(True))
    if source == "native":
        frame.close()
    elif source == "button":
        frame._btn_close.click()
    else:
        QTest.keyClick(frame, Qt.Key_Escape)
    assert closed == [True]
    assert not frame._translating and not frame.isVisible()


def test_tray_activation_restores_and_menu_controls_work(qapp):
    tray = SystemTray()
    commands = []
    tray.show_frame_requested.connect(lambda: commands.append("show"))
    tray.toggle_requested.connect(lambda: commands.append("toggle"))
    tray.pause_toggle_requested.connect(lambda: commands.append("pause"))
    tray.copy_requested.connect(lambda: commands.append("copy"))
    for reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick, QSystemTrayIcon.Context):
        tray._on_activated(reason)
    menu = tray._tray.contextMenu()
    menu.defaultAction().trigger()
    tray._action_toggle.trigger()
    tray.set_active(True)
    tray._action_pause.trigger()
    next(a for a in menu.actions() if a.text() == "Скопировать перевод").trigger()
    assert commands == ["show", "show", "show", "toggle", "pause", "copy"]
    tray.set_paused(True)
    assert tray._action_pause.text() == "Возобновить перевод"
    tray.set_active(False)
    assert not tray._action_pause.isEnabled()


def test_shortcut_hints_show_current_bindings(qapp):
    frame = RegionFrame()
    frame.set_pause_shortcut("F7")
    frame.set_paused(True)
    assert frame._btn_pause.toolTip() == "Возобновить (F7)"
    frame.set_pause_shortcut("")
    assert frame._btn_pause.toolTip() == "Возобновить"
    tray = SystemTray()
    tray.set_shortcuts("", "", "F8", "F9")
    with patch.object(tray._tray, "show"), patch.object(tray._tray, "showMessage") as notification:
        tray.show()
    text = notification.call_args.args[1]
    assert "F8" in text and "F9" in text and "Ctrl" not in text
