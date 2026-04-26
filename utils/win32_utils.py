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
