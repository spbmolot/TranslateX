"""
PERF-007: detector-skip — recognize-only по кэшированным боксам.

Уровень джоба: OcrJobThread с cached_boxes не зовёт detect, масштабирует
боксы под downscale и возвращает свежие боксы только при полном прогоне.
Уровень pipeline: политика «не больше 2 recognize-only подряд» +
инвалидация кэша при слабом результате.
"""
from __future__ import annotations

import configparser
import os
import sys
from unittest.mock import MagicMock

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QRect
from PyQt5.QtWidgets import QApplication

import core.pipeline as pipeline_module
from core.ocr import OcrJobThread, TextBlock
from core.pipeline import AppController


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


class _SplitReader:
    """Мок с detect/recognize: считает вызовы, запоминает переданные боксы."""

    def __init__(self):
        self.detect_calls = 0
        self.recognize_calls = 0
        self.last_h_list = None

    def detect(self, image, **kw):
        self.detect_calls += 1
        h, w = image.shape[:2]
        return [[[10, w // 2, 10, 40]]], [[]]  # один горизонтальный бокс

    def recognize(self, image, horizontal_list, free_list, detail=1,
                  paragraph=False, **kw):
        self.recognize_calls += 1
        self.last_h_list = horizontal_list
        x_min, x_max, y_min, y_max = horizontal_list[0]
        bbox = [[x_min, y_min], [x_max, y_min], [x_max, y_max], [x_min, y_max]]
        return [(bbox, "hello", 0.9)]


def _run_job(reader, img, cached_boxes=None, downscale="off"):
    job = OcrJobThread(reader=reader, image=img, min_confidence=0.4,
                       downscale=downscale, cached_boxes=cached_boxes)
    results = []
    job.ocr_complete.connect(results.append)
    job.run()
    return job, results[0]


def test_full_run_calls_detect_and_caches_boxes(qapp):
    reader = _SplitReader()
    img = np.zeros((200, 400, 3), dtype=np.uint8)
    job, blocks = _run_job(reader, img)
    assert reader.detect_calls == 1
    assert reader.recognize_calls == 1
    assert job.last_boxes is not None
    assert job.last_boxes[0] == [[10, 200, 10, 40]]  # координаты кадра
    assert len(blocks) == 1


def test_cached_boxes_skip_detect(qapp):
    reader = _SplitReader()
    img = np.zeros((200, 400, 3), dtype=np.uint8)
    cached = ([[10, 200, 10, 40]], [])
    job, blocks = _run_job(reader, img, cached_boxes=cached)
    assert reader.detect_calls == 0          # детектор пропущен
    assert reader.recognize_calls == 1
    assert job.last_boxes is None            # свежих боксов нет
    assert job.used_cached_boxes is True
    assert len(blocks) == 1


def test_cached_boxes_scaled_for_downscale(qapp):
    """Кэш в координатах кадра; при downscale 0.5 recognize получает ×0.5,
    а выходные bbox возвращаются обратно в координаты кадра."""
    reader = _SplitReader()
    img = np.zeros((1440, 2560, 3), dtype=np.uint8)
    cached = ([[100, 400, 100, 140]], [])
    job, blocks = _run_job(reader, img, cached_boxes=cached, downscale="auto")
    assert reader.last_h_list == [[50, 200, 50, 70]]   # ×0.5 внутрь
    assert blocks[0].bbox[0] == [100, 100]              # ×2 обратно


# ── Политика pipeline ───────────────────────────────────────────────────

@pytest.fixture
def controller(qapp, monkeypatch):
    monkeypatch.setattr(pipeline_module, "OcrWorker", MagicMock())
    cfg = configparser.ConfigParser()
    cfg["general"] = {"capture_interval_ms": "500",
                      "cache_ttl_seconds": "3600", "cache_max_size": "100"}
    cfg["languages"] = {"source_lang": "auto", "target_lang": "ru"}
    cfg["translation"] = {"provider": "google", "deepl_api_key": ""}
    cfg["overlay"] = {"min_confidence": "0.4"}
    cfg["ocr"] = {"gpu": "false", "detector_skip": "true"}
    c = AppController(cfg)
    yield c
    c._timer.stop()


def test_policy_two_skips_then_full_detect(controller):
    boxes = ([[1, 2, 3, 4]], [])
    controller._last_det_boxes = boxes
    assert controller._next_cached_boxes() is boxes   # skip #1
    assert controller._next_cached_boxes() is boxes   # skip #2
    assert controller._next_cached_boxes() is None    # полный detect
    assert controller._next_cached_boxes() is boxes   # цикл заново


def test_policy_disabled_by_config(controller):
    controller._config.set("ocr", "detector_skip", "false")
    controller._last_det_boxes = ([[1, 2, 3, 4]], [])
    assert controller._next_cached_boxes() is None


def test_weak_recognize_only_result_invalidates_cache(controller):
    controller._last_det_boxes = ([[1, 2, 3, 4]], [])
    fake_job = MagicMock()
    fake_job.last_boxes = None
    fake_job.used_cached_boxes = True
    controller.sender = lambda: fake_job
    controller._active = False  # ранний выход после учёта боксов
    controller._on_ocr_complete([])  # пустой результат → кэш сброшен
    assert controller._last_det_boxes is None


def test_full_detect_result_updates_cache(controller):
    fake_job = MagicMock()
    fake_job.last_boxes = ([[5, 6, 7, 8]], [])
    fake_job.used_cached_boxes = False
    controller.sender = lambda: fake_job
    controller._active = False
    controller._on_ocr_complete([])
    assert controller._last_det_boxes == ([[5, 6, 7, 8]], [])
