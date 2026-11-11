"""Глобальные хоткеи: разбор строк и регистрация через RegisterHotKey."""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Win32 only")

from PyQt5.QtWidgets import QApplication

from utils.hotkeys import (
    MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, WM_HOTKEY,
    GlobalHotkeys, parse_hotkey,
)


@pytest.fixture(scope="session")
def qapp():
    yield QApplication.instance() or QApplication(sys.argv)


@pytest.mark.parametrize("text, expected", [
    ("ctrl+shift+t", (MOD_CONTROL | MOD_SHIFT, ord("T"))),
    ("Ctrl + Shift + Space", (MOD_CONTROL | MOD_SHIFT, 0x20)),
    ("alt+f4", (MOD_ALT, 0x73)),
    ("win+1", (MOD_WIN, ord("1"))),
    ("f9", (0, 0x78)),
    ("ctrl+alt+page up", (MOD_CONTROL | MOD_ALT, 0x21)),
])
def test_parse_valid(text, expected):
    assert parse_hotkey(text) == expected


@pytest.mark.parametrize("text", [
    "", "ctrl+", "ctrl+shift", "t", "ctrl+a+b", "ctrl+banana", "ctrl++t",
])
def test_parse_invalid(text):
    with pytest.raises(ValueError):
        parse_hotkey(text)


def test_default_hotkeys_parse():
    for hk in ("ctrl+shift+t", "ctrl+shift+p", "ctrl+shift+space",
               "ctrl+shift+c", "ctrl+shift+f"):
        parse_hotkey(hk)


# Редкое сочетание, чтобы не пересечься с запущенными программами
_RARE = "ctrl+alt+shift+win+f23"


def test_register_conflict_and_unregister(qapp):
    first, second = GlobalHotkeys(), GlobalHotkeys()
    try:
        first.register(_RARE, lambda: None)
        with pytest.raises(OSError) as exc:
            second.register(_RARE, lambda: None)
        assert "занято" in exc.value.strerror

        first.unregister_all()
        second.register(_RARE, lambda: None)  # после снятия — свободно
    finally:
        first.close()
        second.close()


def test_register_all_reports_problems_but_registers_rest(qapp):
    hk = GlobalHotkeys()
    try:
        problems = hk.register_all([
            ("Хорошее", _RARE, lambda: None),
            ("Плохое", "ctrl+banana", lambda: None),
        ])
        assert len(problems) == 1 and problems[0].startswith("Плохое")
        assert len(hk._callbacks) == 1
    finally:
        hk.close()


def test_wm_hotkey_invokes_callback(qapp):
    hk = GlobalHotkeys()
    fired = []
    try:
        hk.register(_RARE, lambda: fired.append(True))
        hotkey_id = next(iter(hk._callbacks))
        ctypes.windll.user32.PostMessageW(
            ctypes.wintypes.HWND(hk._hwnd), WM_HOTKEY, hotkey_id, 0)
        for _ in range(20):
            qapp.processEvents()
            if fired:
                break
        assert fired == [True]
    finally:
        hk.close()
