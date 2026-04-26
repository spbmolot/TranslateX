"""
Coordinate and geometry utility functions.
Pure functions — no Qt or platform dependencies at module level.
"""
from typing import List, Tuple


def easyocr_bbox_to_rect(bbox: List[List[int]]) -> Tuple[int, int, int, int]:
    """
    Convert EasyOCR bbox (4 corner points) to (x, y, w, h).
    EasyOCR format: [[x1,y1], [x2,y1], [x2,y2], [x1,y2]]
    """
    xs = [p[0] for p in bbox]
    ys = [p[1] for p in bbox]
    x = min(xs)
    y = min(ys)
    w = max(xs) - x
    h = max(ys) - y
    return x, y, w, h


def scale_rect(
    x: int, y: int, w: int, h: int, scale_x: float, scale_y: float
) -> Tuple[int, int, int, int]:
    """
    Scale a rect by given factors.
    Used to convert physical pixel coordinates (mss) to logical (Qt) on HiDPI.
    """
    return (
        int(x / scale_x),
        int(y / scale_y),
        int(w / scale_x),
        int(h / scale_y),
    )


def region_relative_rect(
    x: int, y: int, w: int, h: int,
    region_x: int, region_y: int,
) -> Tuple[int, int, int, int]:
    """
    Translate absolute screen coordinates to coordinates relative to a capture region origin.
    Used for positioning text blocks inside the overlay widget.
    """
    return x - region_x, y - region_y, w, h


def clamp_rect(
    x: int, y: int, w: int, h: int,
    bounds_w: int, bounds_h: int,
) -> Tuple[int, int, int, int]:
    """Clamp a rect so it does not exceed the given bounds dimensions."""
    x = max(0, x)
    y = max(0, y)
    w = min(w, bounds_w - x)
    h = min(h, bounds_h - y)
    return x, y, max(w, 0), max(h, 0)
