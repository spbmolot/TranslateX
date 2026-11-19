"""
Дедуп OCR-сигнатуры в AppController._on_ocr_complete:
  • сигнатура нормализована (регистр/пробелы OCR-дрожания не триггерят перевод);
  • при том же тексте, но сдвинутых координатах (скролл) — overlay получает
    блоки с НОВЫМИ bbox и старыми переводами (раньше уезжали старые bbox).
"""
from __future__ import annotations

import configparser
import os
import sys
from unittest.mock import MagicMock

import pytest
import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QRect
from PyQt5.QtWidgets import QApplication

import core.pipeline as pipeline_module
from core.pipeline import AppController
from core.ocr import TextBlock
from core.translator import TranslatedBlock


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture
def controller(qapp, monkeypatch):
    monkeypatch.setattr(pipeline_module, "OcrWorker", MagicMock())
    cfg = configparser.ConfigParser()
    cfg["general"] = {"capture_interval_ms": "500",
                      "cache_ttl_seconds": "3600", "cache_max_size": "100"}
    cfg["languages"] = {"source_lang": "auto", "target_lang": "ru"}
    cfg["translation"] = {"provider": "google", "deepl_api_key": ""}
    cfg["overlay"] = {"min_confidence": "0.4"}
    cfg["ocr"] = {"gpu": "false", "group_paragraphs": "false"}
    c = AppController(cfg)
    c._active = True
    c.set_region(QRect(0, 0, 500, 300))
    yield c
    c._timer.stop()


def _tb(text, y):
    return TextBlock(text=text,
                     bbox=[[10, y], [200, y], [200, y + 20], [10, y + 20]],
                     confidence=0.9)


def _translated(text, translated, y):
    return TranslatedBlock(original=text, translated=translated,
                           bbox=[[10, y], [200, y], [200, y + 20], [10, y + 20]],
                           confidence=0.9)


def test_scroll_refreshes_bboxes_keeps_translations(controller):
    """Тот же текст, но сдвинут на 80px вверх (скролл): без нового перевода,
    но с новыми координатами."""
    controller._last_ocr_signature = ("hello world",)
    controller._last_translated_blocks = [_translated("Hello world", "Привет мир", 200)]

    emitted = []
    controller.translation_ready.connect(lambda blocks, region: emitted.append(blocks))

    controller._on_ocr_complete([_tb("Hello world", 120)])  # y: 200 → 120

    assert len(emitted) == 1
    block = emitted[0][0]
    assert block.translated == "Привет мир"        # перевод переиспользован
    assert block.bbox[0][1] == 120                  # bbox обновлён (не 200)
    # Переводческий джоб не запускался
    assert controller._current_trans_job is None


def test_signature_is_normalized_against_ocr_jitter(controller):
    """OCR-дрожание 'Hello  World' ↔ 'hello world' не должно перезапускать
    перевод."""
    controller._last_ocr_signature = ("hello world",)
    controller._last_translated_blocks = [_translated("Hello world", "Привет мир", 100)]

    emitted = []
    controller.translation_ready.connect(lambda blocks, region: emitted.append(blocks))

    controller._on_ocr_complete([_tb("HELLO  World", 100)])

    assert len(emitted) == 1
    assert emitted[0][0].translated == "Привет мир"
    assert controller._current_trans_job is None


def test_changed_text_starts_translation_job(controller, monkeypatch):
    """Реально новый текст — сигнатуры не совпали, перевод стартует
    (класс замокан, чтобы тест не ходил в сеть)."""
    fake_job_cls = MagicMock()
    monkeypatch.setattr(pipeline_module, "TranslationJobThread", fake_job_cls)

    controller._last_ocr_signature = ("hello world",)
    controller._last_translated_blocks = [_translated("Hello world", "Привет мир", 100)]

    controller._on_ocr_complete([_tb("Goodbye world", 100)])

    fake_job_cls.assert_called_once()
    assert controller._current_trans_job is not None
    assert controller._trans_job_seq == 1
    controller._current_trans_job.start.assert_called_once()


def test_stale_translation_result_dropped(controller):
    """PERF-010: результат джоба #1 отбрасывается, если #2 уже применён."""
    controller._trans_job_seq = 2
    controller._last_applied_trans_id = 2

    emitted = []
    controller.translation_ready.connect(lambda blocks, region: emitted.append(blocks))

    class _FakeSender:
        _job_id = 1

    # Подменяем sender() на устаревший джоб
    controller.sender = lambda: _FakeSender()
    controller._on_translation_complete([_translated("x", "y", 10)])
    assert emitted == []  # результат №1 не должен попасть на overlay

    # А актуальный (№3 > применённого №2) — применяется
    _FakeSender._job_id = 3
    controller._trans_job_seq = 3
    controller._on_translation_complete([_translated("x", "y2", 10)])
    assert len(emitted) == 1
    assert controller._last_applied_trans_id == 3


def test_scaled_capture_samples_native_pixels_and_emits_logical_boxes(controller, monkeypatch):
    region = QRect(-1180, 200, 120, 80)
    native = QRect(-1130, 300, 180, 120)
    controller.set_region(region, native)
    image = np.zeros((120, 180, 3), dtype=np.uint8)
    image[57:79, 87:124] = (60, 90, 120)
    capture = MagicMock(return_value=image)
    monkeypatch.setattr(pipeline_module, "capture_to_numpy", capture)
    monkeypatch.setattr(pipeline_module, "OcrJobThread", MagicMock())
    controller._ocr_worker = MagicMock()
    controller._on_timer_tick()
    capture.assert_called_once_with(-1130, 300, 180, 120)

    controller._last_ocr_signature = ("hello",)
    controller._last_translated_blocks = [_translated("hello", "привет", 10)]
    emitted = []
    controller.translation_ready.connect(lambda blocks, rect: emitted.append((blocks, rect)))
    native_box = [[90, 60], [120, 60], [120, 75], [90, 75]]
    controller._on_ocr_complete([TextBlock(text="hello", bbox=native_box, confidence=1.0)])
    (blocks, rect), = emitted
    assert rect == region
    assert blocks[0].bg_rgb == (60, 90, 120)
    assert blocks[0].bbox == [[60, 40], [80, 40], [80, 50], [60, 50]]


def test_scale_change_rejects_inflight_jobs_even_with_same_logical_rect(controller, monkeypatch):
    region = QRect(0, 0, 500, 300)
    controller._trans_job_seq = 1
    controller._ocr_busy = True
    controller._ocr_generation = controller._region_generation
    assert controller.set_region(region, QRect(0, 0, 750, 450))
    assert not controller.set_region(region, QRect(0, 0, 750, 450))
    emitted = []
    controller.translation_ready.connect(lambda blocks, rect: emitted.append(blocks))
    translation_job = MagicMock()
    monkeypatch.setattr(pipeline_module, "TranslationJobThread", translation_job)
    controller._on_ocr_complete([_tb("old", 0)])
    translation_job.assert_not_called()
    controller.sender = lambda: type("OldTranslation", (), {"_job_id": 1})()
    controller._on_translation_partial([_translated("old", "старый", 0)])
    controller._on_translation_complete([_translated("old", "старый", 0)])
    assert not emitted
