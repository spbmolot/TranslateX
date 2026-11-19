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
    """Opt in before loading GUI libraries. Windows 8.1 supports per-monitor v1."""
    try:
        set_context = ctypes.WinDLL("user32", use_last_error=True).SetProcessDpiAwarenessContext
    except AttributeError:
        pass
    else:
        # A manifest may already have set the process context.
        if set_context(ctypes.c_void_p(-4)) or ctypes.get_last_error() == 5:
            return  # Per-monitor v2 is active, or the process context is already set.
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        _user32.SetProcessDPIAware()


def get_dpi_scale(hwnd: int) -> float:
    """Return the DPI scale factor for the monitor containing the given HWND."""
    try:
        dpi = _user32.GetDpiForWindow(ctypes.wintypes.HWND(hwnd))
        return dpi / 96.0 if dpi else 1.0
    except Exception:
        return 1.0


def monitor_bounds_at(x: int, y: int):
    """Physical (x, y, width, height) of the monitor at a native desktop point."""
    from ctypes import wintypes

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    monitor_from_point = _user32.MonitorFromPoint
    monitor_from_point.argtypes = [wintypes.POINT, wintypes.DWORD]
    monitor_from_point.restype = wintypes.HANDLE
    monitor = monitor_from_point(wintypes.POINT(x, y), 0)
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(info)
    if monitor and _user32.GetMonitorInfoW(wintypes.HANDLE(monitor), ctypes.byref(info)):
        rect = info.rcMonitor
        return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top
    return None


# ── Единственный экземпляр приложения ────────────────────────────────────
ERROR_ALREADY_EXISTS = 183
ACTIVATION_TITLE = "TranslateX.SingleInstance.Activate"
WM_SHOW_FRAME = 0x8001  # WM_APP + 1, sent only to our message-only window
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateMutexW.restype = ctypes.wintypes.HANDLE
_kernel32.CreateMutexW.argtypes = [
    ctypes.c_void_p, ctypes.wintypes.BOOL, ctypes.wintypes.LPCWSTR]
_kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]


def request_existing_frame() -> bool:
    """Ask the running instance in this desktop session to surface its controls."""
    from ctypes import wintypes
    find = _user32.FindWindowExW
    find.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
    find.restype = wintypes.HWND
    hwnd = find(wintypes.HWND(-3), None, None, ACTIVATION_TITLE)
    if not hwnd:
        return False
    pid = wintypes.DWORD()
    target = wintypes.HWND(hwnd)
    _user32.GetWindowThreadProcessId(target, ctypes.byref(pid))
    _user32.AllowSetForegroundWindow(pid)
    return bool(_user32.PostMessageW(target, WM_SHOW_FRAME, 0, 0))


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


def window_client_rect(hwnd: int):
    """Return a client rectangle in physical desktop pixels (PM-aware process)."""
    handle = ctypes.wintypes.HWND(hwnd)
    bounds = ctypes.wintypes.RECT()
    origin = ctypes.wintypes.POINT()
    if (_user32.GetClientRect(handle, ctypes.byref(bounds))
            and _user32.ClientToScreen(handle, ctypes.byref(origin))):
        return origin.x, origin.y, bounds.right, bounds.bottom
    return None


def set_frameless_rect(hwnd: int, x: int, y: int, w: int, h: int) -> bool:
    """Position a frameless HWND in native pixels, retaining focus and Z order."""
    return bool(_user32.SetWindowPos(ctypes.wintypes.HWND(hwnd), None,
                                    x, y, w, h, 0x0014))  # SWP_NOACTIVATE | SWP_NOZORDER
