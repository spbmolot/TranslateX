"""
E2E-прогон реального GUI: поднимает настоящий App (main.App), показывает
окно с английским текстом, программно жмёт «Старт» и сохраняет скриншоты
до/после перевода.

Запуск: python scripts/e2e_gui_check.py [outdir] [visual|combat]
  visual (дефолт): exclude_from_capture=false — overlay ВИДЕН на скриншоте
    (демонстрация того, что видит пользователь);
  combat: боевой конфиг (exclude on, opaque overlay) — скриншот должен
    показать ОРИГИНАЛ (overlay исключён из захвата → нет петли OCR),
    а перевод подтверждается текстом в stdout.
Выход: 0 = перевод отрисован; 1 = таймаут/ошибка (детали в stdout).
"""
import configparser
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OUTDIR = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
MODE = sys.argv[2] if len(sys.argv) > 2 else "visual"   # visual | combat
BACKEND = sys.argv[3] if len(sys.argv) > 3 else "easyocr"  # easyocr | onnx
CONTENT = (100, 150, 620, 260)   # окно с текстом (x, y, w, h)
SHOT = (60, 100, 700, 380)       # область скриншота
TIMEOUT_S = 300


def make_config() -> configparser.ConfigParser:
    c = configparser.ConfigParser()
    c.read_dict({
        "general": {
            "hotkey_select": "", "hotkey_toggle": "", "hotkey_pause": "",
            "hotkey_copy": "", "hotkey_fullscreen": "",
            "capture_interval_ms": "1500",
            "cache_ttl_seconds": "3600", "cache_max_size": "500",
        },
        "languages": {"source_lang": "en", "target_lang": "ru"},
        "translation": {"provider": "google", "deepl_api_key": ""},
        "overlay": {
            "style": "inpaint",
            # visual: overlay в захвате (для скриншота); combat: боевой WDA
            "exclude_from_capture": "false" if MODE == "visual" else "true",
            "opacity": "0.0", "font_size": "13", "auto_font": "true",
            "font_color": "#ffffff", "bg_color": "#1a1a2e",
            "min_confidence": "0.4",
        },
        "ocr": {"backend": BACKEND, "gpu": "false",
                "group_paragraphs": "true", "downscale": "auto",
                "detector_skip": "true"},
        "ui": {"theme": "auto"},
    })
    return c


def screenshot(name: str) -> Path:
    from core.capture import capture_region
    from PyQt5.QtCore import QRect
    from PyQt5.QtWidgets import QApplication
    from utils.dpi_geometry import logical_to_native
    rect = QRect(*SHOT)
    screen = QApplication.screenAt(rect.center()) or QApplication.primaryScreen()
    img = capture_region(*logical_to_native(rect, screen).getRect())
    path = OUTDIR / name
    img.save(str(path))
    print(f"[e2e] screenshot: {path}")
    return path


def main() -> int:
    import main as app_main  # torch до PyQt5, логирование, DPI

    from PyQt5.QtCore import QRect, Qt, QTimer
    from PyQt5.QtGui import QFont
    from PyQt5.QtWidgets import QLabel

    qapp = app_main.create_qapplication(sys.argv[:1])
    qapp.setQuitOnLastWindowClosed(False)

    # ── Контент: окно с английским текстом ────────────────────────────
    label = QLabel(
        "The quick brown fox jumps\n"
        "over the lazy dog. Screen\n"
        "translation should feel instant.\n"
        "\n"
        "Second paragraph is here to\n"
        "check paragraph grouping."
    )
    label.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
    label.setStyleSheet(
        "background-color: #fdfdfd; color: #111111; padding: 14px;")
    label.setFont(QFont("Segoe UI", 14))
    x, y, w, h = CONTENT
    label.setGeometry(x, y, w, h)
    label.show()

    app = app_main.App(qapp, make_config())

    state = {"translations": 0, "blocks": 0, "done": False}
    t0 = time.time()

    def on_translation(blocks, region):
        state["translations"] += 1
        state["blocks"] = len(blocks)
        print(f"[e2e] translation_ready #{state['translations']}: "
              f"{len(blocks)} block(s) at {time.time()-t0:.1f}s")
        if state["translations"] == 1 and not state["done"]:
            # Дать overlay отрисоваться и замереть, затем скриншот
            QTimer.singleShot(1200, finish_ok)

    def finish_ok():
        if state["done"]:
            return
        state["done"] = True
        app._controller.pause()  # заморозить пайплайн, overlay остаётся
        qapp.processEvents()
        time.sleep(0.3)
        qapp.processEvents()
        screenshot(f"e2e_after_{MODE}_{BACKEND}.png")
        text = app._controller.last_translation_text()
        print(f"[e2e] last_translation_text ({len(text)} chars):")
        for line in text.splitlines():
            print(f"        {line}")
        print(f"[e2e] SUCCESS in {time.time()-t0:.1f}s")
        app._quit()

    def on_timeout():
        if state["done"]:
            return
        state["done"] = True
        print(f"[e2e] TIMEOUT after {TIMEOUT_S}s: "
              f"translations={state['translations']}")
        screenshot("e2e_timeout.png")
        app._quit()
        sys.exit(1)

    app._controller.translation_ready.connect(on_translation)

    def position_and_start():
        # Рамка вокруг контента (внутренняя область захвата = окно текста)
        app._frame.show_at(QRect(x + 6, y + 6, w - 12, h - 12))
        qapp.processEvents()
        screenshot("e2e_before.png")
        app._frame.set_translating(True)
        app._start_translation()
        print(f"[e2e] started at {time.time()-t0:.1f}s (ждём OCR + перевод…)")

    QTimer.singleShot(1500, position_and_start)
    QTimer.singleShot(TIMEOUT_S * 1000, on_timeout)

    return qapp.exec_()


if __name__ == "__main__":
    sys.exit(main())
