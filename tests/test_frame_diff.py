"""
Тесты для frame-diff guard в AppController.
Проверяем `_frame_signature` и `_frame_unchanged` без поднятия Qt/EasyOCR —
обе функции работают только с numpy-массивами и не требуют контекста.
"""
import numpy as np
import pytest

from core.pipeline import AppController


# ── _frame_signature ────────────────────────────────────────────────────

def test_signature_shape_is_downsampled():
    img = np.zeros((480, 640, 3), dtype=np.uint8)
    sig = AppController._frame_signature(img)
    # 480//32 = 15, 640//32 = 20 → результат 32×32 (по stride)
    assert sig.ndim == 2
    assert sig.shape[0] <= 32 + 1  # допуск на stride-округление
    assert sig.shape[1] <= 32 + 1
    assert sig.dtype == np.float32


def test_signature_handles_small_images():
    # Меньше 32 по любой оси — stride=1, размер совпадает
    img = np.zeros((10, 10, 3), dtype=np.uint8)
    sig = AppController._frame_signature(img)
    assert sig.shape == (10, 10)


def test_signature_handles_grayscale():
    # 2D без канала — функция должна работать тоже
    img = np.zeros((100, 100), dtype=np.uint8)
    sig = AppController._frame_signature(img)
    assert sig.ndim == 2


def test_signature_identical_images_match():
    img = np.full((220, 640, 3), 128, dtype=np.uint8)
    a = AppController._frame_signature(img)
    b = AppController._frame_signature(img.copy())
    assert np.allclose(a, b)


def test_signature_different_images_differ():
    img1 = np.zeros((220, 640, 3), dtype=np.uint8)
    img2 = np.full((220, 640, 3), 200, dtype=np.uint8)
    a = AppController._frame_signature(img1)
    b = AppController._frame_signature(img2)
    assert not np.allclose(a, b)
    # Среднее различие должно быть значительным
    assert float(np.abs(a - b).mean()) > 100


# ── _frame_unchanged (требует instance) ────────────────────────────────

class _FakeController:
    """
    Минимальный «контейнер» для тестирования _frame_unchanged без QObject.
    Дублирует поведение `AppController._frame_unchanged`, но не наследует
    Qt — иначе тест требовал бы QApplication.
    """
    _last_frame_signature = None
    _frame_signature = staticmethod(AppController._frame_signature)
    _frame_unchanged = AppController._frame_unchanged


def test_unchanged_first_call_returns_false():
    c = _FakeController()
    img = np.zeros((100, 100, 3), dtype=np.uint8)
    # Первый вызов — нечего сравнивать, всегда False
    assert c._frame_unchanged(img) is False
    # После первого вызова сигнатура сохранена
    assert c._last_frame_signature is not None


def test_unchanged_identical_frames_returns_true():
    c = _FakeController()
    img = np.full((100, 100, 3), 50, dtype=np.uint8)
    c._frame_unchanged(img)               # populate signature
    assert c._frame_unchanged(img) is True


def test_unchanged_different_frames_returns_false():
    c = _FakeController()
    img1 = np.zeros((100, 100, 3), dtype=np.uint8)
    img2 = np.full((100, 100, 3), 200, dtype=np.uint8)
    c._frame_unchanged(img1)
    assert c._frame_unchanged(img2) is False


def test_unchanged_tolerates_small_noise():
    """Шум JPEG / субпиксельные сдвиги не должны триггерить OCR."""
    c = _FakeController()
    rng = np.random.default_rng(42)
    base = np.full((100, 100, 3), 128, dtype=np.uint8)
    noisy = np.clip(
        base.astype(np.int16) + rng.integers(-1, 2, base.shape), 0, 255
    ).astype(np.uint8)
    c._frame_unchanged(base)
    # Шум ±1 — diff < threshold=2.0 → unchanged
    assert c._frame_unchanged(noisy) is True


def test_unchanged_detects_real_change():
    """Появление текста в углу — реальное изменение, OCR должен запуститься."""
    c = _FakeController()
    blank = np.zeros((100, 100, 3), dtype=np.uint8)
    with_text = blank.copy()
    with_text[20:40, 20:80] = 255  # белый прямоугольник = «новый текст»
    c._frame_unchanged(blank)
    assert c._frame_unchanged(with_text) is False


def test_unchanged_detects_small_local_change_in_large_frame():
    """Регрессия: одно изменившееся слово в большом регионе.
    Глобальный mean размывал такой diff до <2 и кадр считался неизменным
    (до ~45 с устаревшего перевода). Тайловый максимум обязан заметить."""
    c = _FakeController()
    big = np.full((1080, 1920, 3), 240, dtype=np.uint8)  # светлый фон
    changed = big.copy()
    changed[500:522, 800:920] = 30  # «слово» ~120×22 px сменило цвет
    c._frame_unchanged(big)
    # Проверим, что глобальный mean действительно был бы слеп (доказательство бага)
    sig_a = AppController._frame_signature(big)
    sig_b = AppController._frame_signature(changed)
    assert float(np.abs(sig_a - sig_b).mean()) < 2.0
    # ...а тайловый метод изменение видит
    assert c._frame_unchanged(changed) is False


def test_unchanged_resets_on_shape_mismatch():
    """Смена размера региона — сигнатура другой формы, должна вернуть False."""
    c = _FakeController()
    img1 = np.zeros((100, 100, 3), dtype=np.uint8)
    img2 = np.zeros((220, 640, 3), dtype=np.uint8)
    c._frame_unchanged(img1)
    assert c._frame_unchanged(img2) is False
