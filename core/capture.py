"""
Screen capture using mss.
Fast (<10ms per frame), thread-safe when called with a new mss instance per call.
"""
import logging
from typing import Tuple

import mss
import mss.tools
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)


def capture_region(x: int, y: int, width: int, height: int) -> Image.Image:
    """
    Capture a screen region and return as RGB PIL Image.
    Creates a new mss instance per call for thread safety.
    """
    monitor = {"left": x, "top": y, "width": width, "height": height}
    with mss.mss() as sct:
        raw = sct.grab(monitor)
        # mss returns BGRA — convert to RGB
        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
    return img


def capture_to_numpy(x: int, y: int, width: int, height: int) -> np.ndarray:
    """Capture region and return as numpy RGB array (for EasyOCR)."""
    img = capture_region(x, y, width, height)
    return np.array(img)
