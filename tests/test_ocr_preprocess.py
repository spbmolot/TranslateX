"""Тесты препроцессинга кадра перед OCR (PERF-002) и обратного масштаба bbox."""
import numpy as np
import pytest

from core.ocr import OcrJobThread, preprocess_for_ocr


def _img(h, w):
    return np.zeros((h, w, 3), dtype=np.uint8)


# ── preprocess_for_ocr ──────────────────────────────────────────────────

def test_off_returns_original():
    img = _img(2160, 3840)
    out, scale = preprocess_for_ocr(img, "off")
    assert out is img and scale == 1.0


def test_auto_small_frame_untouched():
    img = _img(220, 640)
    out, scale = preprocess_for_ocr(img, "auto")
    assert out is img and scale == 1.0


def test_auto_1440p_scaled_to_720():
    img = _img(1440, 2560)
    out, scale = preprocess_for_ocr(img, "auto")
    assert scale == pytest.approx(0.5)
    assert out.shape[0] == 720


def test_auto_clamped_at_half():
    """4K не сжимаем сильнее ×0.5 — мелкий шрифт перестал бы распознаваться."""
    img = _img(2160, 3840)
    out, scale = preprocess_for_ocr(img, "auto")
    assert scale == pytest.approx(0.5)
    assert out.shape[0] == 1080


def test_explicit_factor():
    img = _img(1000, 1000)
    out, scale = preprocess_for_ocr(img, "0.75")
    assert scale == pytest.approx(0.75)
    assert out.shape[0] == 750


def test_invalid_mode_falls_back_to_original():
    img = _img(1000, 1000)
    out, scale = preprocess_for_ocr(img, "garbage")
    assert out is img and scale == 1.0
    out, scale = preprocess_for_ocr(img, "1.5")  # вне 0.1..1.0
    assert out is img and scale == 1.0


# ── OcrJobThread: bbox возвращаются в координатах исходного кадра ───────

class _FakeReader:
    """Возвращает один бокс в координатах ПЕРЕДАННОГО (сжатого) кадра."""
    def __init__(self):
        self.received_shape = None

    def readtext(self, image, detail=1, paragraph=False):
        self.received_shape = image.shape
        h, w = image.shape[:2]
        # бокс в центре кадра размером 1/4
        return [(
            [[w // 4, h // 4], [w // 2, h // 4], [w // 2, h // 2], [w // 4, h // 2]],
            "hello",
            0.95,
        )]


def test_job_scales_bbox_back_to_original_coords(qt_app=None):
    reader = _FakeReader()
    img = _img(1440, 2560)
    job = OcrJobThread(reader=reader, image=img, min_confidence=0.4,
                       downscale="auto")
    results = []
    job.ocr_complete.connect(results.append)
    job.run()  # синхронно, без start()

    # Reader получил сжатый кадр 720p
    assert reader.received_shape[0] == 720
    blocks = results[0]
    assert len(blocks) == 1
    # bbox отмасштабирован обратно ×2 → координаты исходного 1440p кадра
    (x1, y1), (x2, _), (_, y2), _ = blocks[0].bbox
    assert x1 == pytest.approx(2560 // 4, abs=2)
    assert y1 == pytest.approx(1440 // 4, abs=2)
    assert x2 == pytest.approx(2560 // 2, abs=2)
    assert y2 == pytest.approx(1440 // 2, abs=2)


def test_job_downscale_off_passes_original():
    reader = _FakeReader()
    img = _img(1440, 2560)
    job = OcrJobThread(reader=reader, image=img, min_confidence=0.4,
                       downscale="off")
    results = []
    job.ocr_complete.connect(results.append)
    job.run()
    assert reader.received_shape == (1440, 2560, 3)
