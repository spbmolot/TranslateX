"""
PERF-003: OCR worker создаётся лениво — не в __init__ контроллера,
а при первом start(). Плюс тесты last_translation_text (копирование).
"""
from __future__ import annotations

import configparser
import os
import sys
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QRect
from PyQt5.QtWidgets import QApplication

import core.pipeline as pipeline_module
from core.pipeline import AppController
from core.translator import TranslatedBlock


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture
def worker_cls(monkeypatch):
    fake_cls = MagicMock()
    inst = MagicMock()
    inst._reader = None
    fake_cls.return_value = inst
    monkeypatch.setattr(pipeline_module, "OcrWorker", fake_cls)
    return fake_cls


@pytest.fixture
def controller(qapp, worker_cls):
    cfg = configparser.ConfigParser()
    cfg["general"] = {"capture_interval_ms": "500",
                      "cache_ttl_seconds": "3600", "cache_max_size": "100"}
    cfg["languages"] = {"source_lang": "auto", "target_lang": "ru"}
    cfg["translation"] = {"provider": "google", "deepl_api_key": ""}
    cfg["overlay"] = {"min_confidence": "0.4"}
    cfg["ocr"] = {"gpu": "false"}
    c = AppController(cfg)
    yield c
    c._timer.stop()


def test_worker_not_created_at_init(controller, worker_cls):
    """Создание контроллера НЕ должно поднимать EasyOCR (быстрый cold start)."""
    assert controller._ocr_worker is None
    worker_cls.assert_not_called()


def test_start_creates_worker_and_emits_loading(controller, worker_cls):
    loading = []
    controller.ocr_loading.connect(lambda: loading.append(True))
    controller.set_region(QRect(0, 0, 100, 100))
    controller.start()
    worker_cls.assert_called_once()
    assert controller._ocr_worker is not None
    controller._ocr_worker.start.assert_called_once()
    assert loading == [True]


def test_second_start_does_not_recreate_worker(controller, worker_cls):
    controller.set_region(QRect(0, 0, 100, 100))
    controller.start()
    controller.stop()
    controller.start()
    worker_cls.assert_called_once()  # воркер переживает stop/start


def test_start_without_region_does_not_create_worker(controller, worker_cls):
    controller.start()  # региона нет — ранний выход
    worker_cls.assert_not_called()


def test_shutdown_with_no_worker_is_safe(controller):
    controller.shutdown()  # не должно упасть при _ocr_worker=None


# ── last_translation_text (для хоткея копирования) ─────────────────────

def _tb(text):
    return TranslatedBlock(original="x", translated=text,
                           bbox=[[0, 0], [10, 0], [10, 10], [0, 10]],
                           confidence=0.9)


def test_last_translation_text_empty_by_default(controller):
    assert controller.last_translation_text() == ""


def test_last_translation_text_joins_blocks(controller):
    controller._last_translated_blocks = [_tb("Привет"), _tb(" мир "), _tb("")]
    assert controller.last_translation_text() == "Привет\nмир"
