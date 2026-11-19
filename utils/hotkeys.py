"""
Глобальные горячие клавиши через Win32 RegisterHotKey.

Заменяет библиотеку `keyboard`: та ставит низкоуровневый хук на ВСЮ
клавиатуру (WH_KEYBOARD_LL) — на это реагируют антивирусы, хук добавляет
задержку каждому нажатию в системе и не сообщает о конфликтах сочетаний.
RegisterHotKey — штатный механизм Windows: система сама присылает
WM_HOTKEY только для наших сочетаний и возвращает ошибку, если сочетание
уже занято другой программой.

Отличие в поведении: зарегистрированное сочетание ПЕРЕХВАТЫВАЕТСЯ и до
активного приложения не доходит (так работают все глобальные хоткеи Windows).
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
from typing import Callable, Dict, List, Tuple

from PyQt5.QtCore import QTimer

from core.shortcuts import MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, parse_hotkey
from utils.win32_utils import ACTIVATION_TITLE, WM_SHOW_FRAME

logger = logging.getLogger(__name__)

WM_HOTKEY = 0x0312
MOD_NOREPEAT = 0x4000  # удержание клавиши не шлёт WM_HOTKEY повторно
ERROR_HOTKEY_ALREADY_REGISTERED = 1409

_WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, ctypes.wintypes.HWND, ctypes.c_uint,
    ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM)


class _WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", ctypes.c_uint), ("lpfnWndProc", _WNDPROC),
        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
        ("hInstance", ctypes.wintypes.HINSTANCE), ("hIcon", ctypes.wintypes.HANDLE),
        ("hCursor", ctypes.wintypes.HANDLE), ("hbrBackground", ctypes.wintypes.HANDLE),
        ("lpszMenuName", ctypes.wintypes.LPCWSTR),
        ("lpszClassName", ctypes.wintypes.LPCWSTR),
    ]


_HWND_MESSAGE = ctypes.wintypes.HWND(-3)
_instance_counter = 0


class GlobalHotkeys:
    """
    Скрытое message-only окно — приёмник WM_HOTKEY. Хоткеи привязаны к его
    HWND, поэтому доходят и во время модальных циклов (меню трея,
    перетаскивание окна), когда сообщения потока без окна терялись бы.
    Окно создаётся напрямую через Win32 (не QWidget): не зависит от
    Qt-платформы и не появляется среди окон приложения. Сообщения ему
    доставляет обычный цикл событий Qt. Создавать и использовать — только
    из GUI-потока.
    """

    def __init__(self, on_activate=None):
        global _instance_counter
        _instance_counter += 1
        u32 = self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetModuleHandleW.restype = ctypes.wintypes.HMODULE
        k32.GetModuleHandleW.argtypes = [ctypes.wintypes.LPCWSTR]
        u32.RegisterHotKey.argtypes = [
            ctypes.wintypes.HWND, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
        u32.RegisterHotKey.restype = ctypes.wintypes.BOOL
        u32.UnregisterHotKey.argtypes = [ctypes.wintypes.HWND, ctypes.c_int]
        u32.DefWindowProcW.restype = ctypes.c_ssize_t
        u32.DefWindowProcW.argtypes = [
            ctypes.wintypes.HWND, ctypes.c_uint,
            ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM]
        u32.RegisterClassW.argtypes = [ctypes.POINTER(_WNDCLASSW)]
        u32.RegisterClassW.restype = ctypes.wintypes.ATOM
        u32.CreateWindowExW.restype = ctypes.wintypes.HWND
        u32.CreateWindowExW.argtypes = [
            ctypes.wintypes.DWORD, ctypes.wintypes.LPCWSTR, ctypes.wintypes.LPCWSTR,
            ctypes.wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.wintypes.HWND, ctypes.wintypes.HANDLE,
            ctypes.wintypes.HINSTANCE, ctypes.c_void_p]
        u32.DestroyWindow.argtypes = [ctypes.wintypes.HWND]
        u32.UnregisterClassW.argtypes = [
            ctypes.wintypes.LPCWSTR, ctypes.wintypes.HINSTANCE]

        self._callbacks: Dict[int, Callable[[], None]] = {}
        self._on_activate = on_activate
        self._next_id = 1
        # Ссылку на WNDPROC держим в self: иначе GC соберёт её и Windows
        # вызовет освобождённую память.
        self._wndproc = _WNDPROC(self._on_message)
        self._hinstance = k32.GetModuleHandleW(None)
        self._class_name = f"TranslateXHotkeys_{_instance_counter}"
        wc = _WNDCLASSW()
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = self._hinstance
        wc.lpszClassName = self._class_name
        if not u32.RegisterClassW(ctypes.byref(wc)):
            raise ctypes.WinError(ctypes.get_last_error())
        self._hwnd = u32.CreateWindowExW(
            0, self._class_name, ACTIVATION_TITLE if on_activate else "TranslateX hotkeys",
            0, 0, 0, 0, 0,
            _HWND_MESSAGE, None, self._hinstance, None)
        if not self._hwnd:
            raise ctypes.WinError(ctypes.get_last_error())

    def _on_message(self, hwnd, msg, wparam, lparam):
        if msg == WM_SHOW_FRAME and self._on_activate is not None:
            QTimer.singleShot(0, self._on_activate)
            return 0
        if msg == WM_HOTKEY:
            try:
                callback = self._callbacks.get(int(wparam))
                if callback is not None:
                    # Выходим из обработчика нативного сообщения: callback
                    # может открыть модальное окно (селектор области).
                    QTimer.singleShot(0, callback)
            except Exception:
                logger.exception("Ошибка обработки WM_HOTKEY")
            return 0
        return self._user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def close(self) -> None:
        """Снять хоткеи и уничтожить окно-приёмник."""
        self.unregister_all()
        if self._hwnd:
            self._user32.DestroyWindow(self._hwnd)
            self._user32.UnregisterClassW(self._class_name, self._hinstance)
            self._hwnd = None

    def register(self, hotkey: str, callback: Callable[[], None]) -> None:
        """Регистрирует сочетание. Бросает ValueError (не разобрано) или
        OSError (занято другой программой / отказ системы)."""
        mods, vk = parse_hotkey(hotkey)
        hotkey_id = self._next_id
        if not self._user32.RegisterHotKey(self._hwnd, hotkey_id, mods | MOD_NOREPEAT, vk):
            err = ctypes.get_last_error()
            if err == ERROR_HOTKEY_ALREADY_REGISTERED:
                raise OSError(err, "сочетание уже занято другой программой")
            raise OSError(err, f"система отклонила сочетание (код {err})")
        self._next_id += 1
        self._callbacks[hotkey_id] = callback

    def register_all(self, bindings: List[Tuple[str, str, Callable[[], None]]]) -> List[str]:
        """bindings: (название действия, сочетание, callback). Возвращает
        список человекочитаемых ошибок по незарегистрированным сочетаниям."""
        problems: List[str] = []
        for title, hotkey, callback in bindings:
            if not hotkey.strip():
                continue
            try:
                self.register(hotkey, callback)
            except (ValueError, OSError) as e:
                reason = e.strerror if isinstance(e, OSError) and e.strerror else str(e)
                problems.append(f"{title} ({hotkey}): {reason}")
        return problems

    def unregister_all(self) -> None:
        for hotkey_id in list(self._callbacks):
            self._user32.UnregisterHotKey(self._hwnd, hotkey_id)
        self._callbacks.clear()
