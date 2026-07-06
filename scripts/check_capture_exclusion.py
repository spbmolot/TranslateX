"""
Эмпирическая проверка WDA_EXCLUDEFROMCAPTURE на текущей машине.

Создаёт ярко-красное окно, захватывает его область через mss (BitBlt)
до и после SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE) и сравнивает.

Выход:
  EXCLUSION WORKS     — окно исчезло из захвата: overlay можно не прятать.
  EXCLUSION FAILED    — окно всё равно в кадре: нужен fallback hide/show.
  CAPTURE UNAVAILABLE — mss не смог захватить экран (headless?).

Запуск: python scripts/check_capture_exclusion.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np


def main() -> int:
    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import QApplication, QWidget

    from core.capture import capture_to_numpy
    from utils.win32_utils import get_hwnd, set_exclude_from_capture

    app = QApplication(sys.argv)

    w = QWidget()
    w.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
    w.setStyleSheet("background-color: #ff0010;")
    x, y, side = 200, 200, 120
    w.setGeometry(x, y, side, side)
    w.show()
    app.processEvents()
    time.sleep(0.6)  # даём DWM дорисовать
    app.processEvents()

    def red_share() -> float:
        img = capture_to_numpy(x + 10, y + 10, side - 20, side - 20)
        r, g, b = img[..., 0].astype(int), img[..., 1].astype(int), img[..., 2].astype(int)
        red = (r > 180) & (g < 90) & (b < 90)
        return float(np.mean(red))

    try:
        before = red_share()
    except Exception as e:
        print(f"CAPTURE UNAVAILABLE: {e}")
        return 2

    if before < 0.5:
        print(f"CAPTURE UNAVAILABLE: тестовое окно не видно в захвате "
              f"(red={before:.0%}) — возможно, headless/RDP без экрана")
        return 2

    ok = set_exclude_from_capture(get_hwnd(w))
    app.processEvents()
    time.sleep(0.6)
    app.processEvents()
    after = red_share()

    print(f"API success={ok}, red before={before:.0%}, after={after:.0%}")
    if ok and after < 0.1:
        print("EXCLUSION WORKS — overlay можно исключать из захвата (без мерцания)")
        return 0
    print("EXCLUSION FAILED — окно всё равно попадает в кадр, нужен hide/show fallback")
    return 1


if __name__ == "__main__":
    sys.exit(main())
