"""
PERF-001: библиотека замеров для бенчмарк-харнесса.

Каждая bench_* функция возвращает dict {"median_ms", "p95_ms", "n"}.
Быстрые бенчи (capture, frame_diff, paragraphs, inpaint, translation_cache_hit)
не требуют сети и моделей. Тяжёлые (OCR) и сетевые (translation_miss) —
opt-in через scripts/run_bench.py.
"""
import os
import statistics
import time
from typing import Callable, Dict, List

# Как в main.py: KMP-разрешение и torch ДО PyQt5, иначе c10.dll падает
# с WinError 1114 (конфликт CRT/OMP DLL между PyQt5 и torch).
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
try:
    import torch  # noqa: F401
except Exception:
    pass

import numpy as np

# ── Утилиты ─────────────────────────────────────────────────────────────


def _timeit(fn: Callable[[], object], n: int, warmup: int = 2) -> Dict:
    for _ in range(warmup):
        fn()
    samples: List[float] = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000.0)
    samples.sort()
    p95_idx = min(len(samples) - 1, int(round(0.95 * (len(samples) - 1))))
    return {
        "median_ms": round(statistics.median(samples), 3),
        "p95_ms": round(samples[p95_idx], 3),
        "n": n,
    }


def make_text_image(width: int = 1280, height: int = 720,
                    font_size: int = 18) -> np.ndarray:
    """Детерминированная синтетическая картинка с английским текстом."""
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (width, height), "#f4f4f4")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", font_size)
    except Exception:
        font = ImageFont.load_default()
    lines = [
        "The quick brown fox jumps over the lazy dog.",
        "Screen translation should feel almost instant.",
        "Paragraph grouping merges wrapped lines into sentences.",
        "Background inpainting hides the original text below.",
        "Performance is measured before and after each change.",
    ]
    y = 40
    i = 0
    while y < height - font_size - 40:
        d.text((40, y), lines[i % len(lines)], fill="#101010", font=font)
        y += font_size + 14
        i += 1
    return np.array(img)


def _synthetic_blocks(count: int = 60):
    """Синтетические построчные TextBlock'и (3 колонки по count/3 строк)."""
    from core.ocr import TextBlock
    blocks = []
    per_col = max(1, count // 3)
    for col in range(3):
        x = 40 + col * 420
        for row in range(per_col):
            y = 40 + row * 26
            blocks.append(TextBlock(
                text=f"line {col}-{row} of sample text",
                bbox=[[x, y], [x + 360, y], [x + 360, y + 20], [x, y + 20]],
                confidence=0.9,
            ))
    return blocks


# ── Бенчи (быстрые, без сети/моделей) ───────────────────────────────────


def bench_capture(n: int = 50) -> Dict:
    """mss.grab 640×220 — типовой регион."""
    from core.capture import capture_to_numpy
    return _timeit(lambda: capture_to_numpy(100, 100, 640, 220), n)


def bench_frame_diff(n: int = 200) -> Dict:
    """Сигнатура + сравнение кадров, 1080p."""
    from core.pipeline import AppController
    img_a = make_text_image(1920, 1080)
    img_b = img_a.copy()
    img_b[500:522, 800:920] = 30

    class _C:
        _last_frame_signature = None
        _last_frame_shape = None
        _frame_signature = staticmethod(AppController._frame_signature)
        _frame_unchanged = AppController._frame_unchanged

    c = _C()

    def one():
        c._frame_unchanged(img_a)
        c._frame_unchanged(img_b)

    return _timeit(one, n)


def bench_paragraphs(n: int = 200) -> Dict:
    """Группировка 60 строк в абзацы."""
    from core.paragraphs import group_paragraphs
    blocks = _synthetic_blocks(60)
    return _timeit(lambda: group_paragraphs(blocks), n)


def bench_inpaint(n: int = 100) -> Dict:
    """Сэмплирование цвета фона: 1080p, 20 блоков."""
    from core.inpaint import apply_bg_colors
    img = make_text_image(1920, 1080)
    blocks = _synthetic_blocks(20)
    return _timeit(lambda: apply_bg_colors(img, blocks), n)


def bench_ocr_preprocess(n: int = 100) -> Dict:
    """PERF-002: даунсэмпл 1440p → 720p."""
    from core.ocr import preprocess_for_ocr
    img = make_text_image(2560, 1440)
    return _timeit(lambda: preprocess_for_ocr(img, "auto"), n)


def bench_translation_cache_hit(n: int = 100) -> Dict:
    """TranslationJobThread.run() при 100% cache hit (без сети)."""
    from core.cache import TranslationCache
    from core.translator import TranslationJobThread
    cache = TranslationCache(max_size=1000, ttl_seconds=36000)
    blocks = _synthetic_blocks(15)
    for b in blocks:
        cache.put(b.text, "ru", f"перевод: {b.text}", "auto", "google")

    def one():
        job = TranslationJobThread(
            blocks=blocks, target_lang="ru", source_lang="auto",
            provider="google", deepl_api_key="", cache=cache,
        )
        job.run()

    return _timeit(one, n)


def bench_ram() -> Dict:
    """Текущий RSS процесса, МБ."""
    import psutil
    rss = psutil.Process().memory_info().rss
    return {"rss_mb": round(rss / (1024 * 1024), 1)}


# ── Тяжёлые бенчи (opt-in) ──────────────────────────────────────────────


def bench_ocr_readtext(n: int = 5, downscale: str = "auto") -> Dict:
    """
    EasyOCR readtext (warm) на синтетическом 2560×1440 PNG (шрифт 28px —
    после auto-даунсэмпла до 720p остаётся читаемым ~14px). 1440p выбран,
    чтобы режимы auto/off реально отличались (на ≤720p auto — no-op).
    Требует установленных моделей в ~/.EasyOCR/model (иначе начнёт качать).
    Инициализация Reader не входит в замер.
    """
    import easyocr
    from core.ocr import preprocess_for_ocr
    reader = easyocr.Reader(["ru", "en"], gpu=False, verbose=False)
    img = make_text_image(2560, 1440, font_size=28)

    def one():
        proc, _scale = preprocess_for_ocr(img, downscale)
        reader.readtext(proc, detail=1, paragraph=False)

    return _timeit(one, n, warmup=1)


def bench_translation_miss_network(n: int = 5) -> Dict:
    """
    РЕАЛЬНЫЙ сетевой перевод 5 строк (Google, без кэша).
    Меряет эффект PERF-004 (параллельность) + PERF-011 (keep-alive).
    Гонять умеренно — free-endpoint банит за частые запросы.
    """
    import random
    from core.cache import TranslationCache
    from core.ocr import TextBlock
    from core.translator import TranslationJobThread

    def one():
        salt = random.randint(0, 10**9)  # уникальные строки → всегда cache miss
        blocks = [
            TextBlock(
                text=f"Example sentence number {i} with salt {salt}.",
                bbox=[[0, i * 20], [100, i * 20], [100, i * 20 + 18], [0, i * 20 + 18]],
                confidence=0.9,
            )
            for i in range(5)
        ]
        job = TranslationJobThread(
            blocks=blocks, target_lang="ru", source_lang="auto",
            provider="google", deepl_api_key="",
            cache=TranslationCache(max_size=10, ttl_seconds=1),
        )
        job.run()

    return _timeit(one, n, warmup=0)


FAST_BENCHES = {
    "capture_640x220": bench_capture,
    "frame_diff_1080p": bench_frame_diff,
    "paragraph_grouping_60": bench_paragraphs,
    "inpaint_sampling_20": bench_inpaint,
    "ocr_preprocess_1440p": bench_ocr_preprocess,
    "translation_cache_hit_15": bench_translation_cache_hit,
}
