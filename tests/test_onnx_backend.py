"""
PERF-006: валидация ONNX-бэкенда OCR.

Тесты скипаются, если модели не экспортированы
(python scripts/export_onnx.py). Проверяют recall на синтетических
картинках (латиница + кириллица) и вменяемость bbox/confidence.
"""
from pathlib import Path

import pytest

MODEL_DIR = Path(__file__).resolve().parent.parent / "assets" / "models" / "onnx"

pytestmark = pytest.mark.skipif(
    not (MODEL_DIR / "craft.onnx").exists(),
    reason="ONNX-модели не экспортированы (scripts/export_onnx.py)",
)


@pytest.fixture(scope="module")
def reader():
    from core.ocr_backends.onnx_backend import OnnxReader
    return OnnxReader(MODEL_DIR)


def _text_image(lines, width=1000, height=400, font_size=24):
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (width, height), "#f8f8f8")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", font_size)
    except Exception:
        font = ImageFont.load_default()
    y = 30
    for line in lines:
        d.text((40, y), line, fill="#101010", font=font)
        y += font_size + 16
    return np.array(img)


def _recall(results, expected_words):
    text = " ".join(t for _, t, _ in results).lower()
    found = [w for w in expected_words if w.lower() in text]
    return len(found) / len(expected_words), text


def test_latin_recall(reader):
    img = _text_image([
        "The quick brown fox jumps",
        "over the lazy dog today.",
        "Screen translation quality test.",
    ])
    results = reader.readtext(img)
    recall, text = _recall(
        results,
        ["quick", "brown", "fox", "jumps", "lazy", "dog",
         "screen", "translation", "quality"],
    )
    assert recall >= 0.9, f"recall={recall:.0%}, распознано: {text!r}"


def test_cyrillic_recall(reader):
    img = _text_image([
        "Быстрая коричневая лиса",
        "перепрыгивает через собаку.",
        "Качество перевода с экрана.",
    ])
    results = reader.readtext(img)
    recall, text = _recall(
        results,
        ["быстрая", "коричневая", "лиса", "перепрыгивает",
         "через", "собаку", "качество", "перевода", "экрана"],
    )
    assert recall >= 0.9, f"recall={recall:.0%}, распознано: {text!r}"


def test_output_format_compatible_with_easyocr(reader):
    """(bbox 4 точки, text, conf) — как у easyocr.Reader.readtext."""
    img = _text_image(["Hello world"])
    results = reader.readtext(img, detail=1, paragraph=False)
    assert len(results) >= 1
    for bbox, text, conf in results:
        assert len(bbox) == 4
        for p in bbox:
            assert len(p) == 2 and isinstance(p[0], int)
        assert isinstance(text, str) and text
        assert 0.0 <= conf <= 1.5


def test_empty_image_returns_no_blocks(reader):
    import numpy as np
    img = np.full((300, 500, 3), 250, dtype=np.uint8)
    assert reader.readtext(img) == []


def test_works_via_ocr_job_thread(reader):
    """OnnxReader взаимозаменяем с easyocr.Reader внутри OcrJobThread."""
    from core.ocr import OcrJobThread
    img = _text_image(["Integration check line"])
    job = OcrJobThread(reader=reader, image=img, min_confidence=0.3,
                       downscale="off")
    results = []
    job.ocr_complete.connect(results.append)
    job.run()
    blocks = results[0]
    assert len(blocks) >= 1
    assert "check" in " ".join(b.text.lower() for b in blocks)
