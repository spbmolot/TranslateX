"""
Win32 API utilities for overlay transparency and click-through.
All ctypes/Win32 calls are isolated here to keep the rest of the codebase platform-agnostic.
"""
import ctypes
import ctypes.wintypes

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
LWA_ALPHA = 0x00000002

# SetWindowDisplayAffinity: исключение окна из захвата экрана (Win10 2004+).
WDA_NONE = 0x00
WDA_EXCLUDEFROMCAPTURE = 0x11

_user32 = ctypes.windll.user32


def get_hwnd(widget) -> int:
    """Return the Win32 HWND for a QWidget."""
    return int(widget.winId())


def set_click_through(hwnd: int) -> None:
    """
    Make a window click-through (transparent to mouse events).
    Must be called AFTER the window is shown (HWND must exist).
    """
    style = _user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    _user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED | WS_EX_TRANSPARENT)


def unset_click_through(hwnd: int) -> None:
    """Remove click-through — makes overlay interactive again."""
    style = _user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    _user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style & ~WS_EX_TRANSPARENT)


def set_exclude_from_capture(hwnd: int) -> bool:
    """
    Исключить окно из захвата экрана (BitBlt/mss, Graphics Capture, DXGI).
    Окно остаётся видимым пользователю, но не попадает в скриншоты —
    overlay не нужно прятать перед каждым захватом кадра (убирает мерцание).

    Требует Windows 10 2004+ и НЕ работает без DWM-композиции.
    Возвращает True при успехе; False → вызывающий код должен использовать
    fallback hide/show.

    ВАЖНО (проверено эмпирически, scripts/check_capture_exclusion.py):
    - обычные окна и окна с WS_EX_LAYERED|WS_EX_TRANSPARENT (click-through
      через SetWindowLong) — РАБОТАЕТ, в т.ч. с setMask;
    - окна с Qt WA_TranslucentBackground (per-pixel alpha через
      UpdateLayeredWindow) — API возвращает False, окно остаётся в захвате.
      Поэтому overlay в inpaint-режиме создаётся НЕпрозрачным с маской.
    """
    try:
        result = _user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
        return bool(result)
    except Exception:
        return False


def unset_exclude_from_capture(hwnd: int) -> None:
    """Вернуть окно в обычный режим (снова попадает в захват)."""
    try:
        _user32.SetWindowDisplayAffinity(hwnd, WDA_NONE)
    except Exception:
        pass


def set_dpi_awareness() -> None:
    """
    Set process DPI awareness to Per-Monitor v2.
    Must be called before QApplication is created.
    """
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def get_dpi_scale(hwnd: int) -> float:
    """Return the DPI scale factor for the monitor containing the given HWND."""
    try:
        dpi = ctypes.windll.shcore.GetDpiForWindow(hwnd)
        return dpi / 96.0
    except Exception:
        return 1.0


# ── Единственный экземпляр приложения ────────────────────────────────────
ERROR_ALREADY_EXISTS = 183
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateMutexW.restype = ctypes.wintypes.HANDLE
_kernel32.CreateMutexW.argtypes = [
    ctypes.c_void_p, ctypes.wintypes.BOOL, ctypes.wintypes.LPCWSTR]
_kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]


def acquire_single_instance(name: str):
    """
    Захватывает именованный мьютекс сессии пользователя.

    Возвращает handle (его нужно держать живым до выхода из процесса —
    Windows освободит мьютекс сама при завершении) или None, если
    приложение с таким именем уже запущено в этой сессии.
    При сбое самого API возвращает 0 — запуск не блокируем.
    """
    handle = _kernel32.CreateMutexW(None, False, f"Local\\{name}")
    if not handle:
        return 0
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        _kernel32.CloseHandle(handle)
        return None
    return handle
