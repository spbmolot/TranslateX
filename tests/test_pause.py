"""
Тесты для AppController.toggle_pause / pause / resume.

AppController.__init__ создаёт OcrWorker и стартует thread — для unit-теста
это слишком тяжело. Подменяем OcrWorker на лёгкий мок до создания
контроллера через monkeypatch на уровне модуля.
"""
from __future__ import annotations

import configparser
import os
import sys
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

import core.pipeline as pipeline_module
from core.pipeline import AppController


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture
def controller(qapp, monkeypatch, tmp_path):
    """Лёгкий AppController с замоканным OcrWorker (без EasyOCR)."""
    fake_worker_cls = MagicMock()
    fake_instance = MagicMock()
    fake_instance._reader = None
    fake_instance.ready = MagicMock()
    fake_instance.ready.connect = MagicMock()
    fake_instance.error = MagicMock()
    fake_instance.error.connect = MagicMock()
    fake_instance.start = MagicMock()
    fake_instance.stop = MagicMock()
    fake_worker_cls.return_value = fake_instance
    monkeypatch.setattr(pipeline_module, "OcrWorker", fake_worker_cls)

    # Кэш переводов в tmp_path, чтобы не трогать пользовательский
    monkeypatch.setattr(
        pipeline_module.TranslationCache, "_default_path",
        lambda self: str(tmp_path / "cache.json"),
        raising=False,
    )

    cfg = configparser.ConfigParser()
    cfg["general"] = {
        "capture_interval_ms": "500",
        "cache_ttl_seconds": "3600",
        "cache_max_size": "100",
    }
    cfg["languages"] = {"source_lang": "auto", "target_lang": "ru"}
    cfg["translation"] = {"provider": "google", "deepl_api_key": ""}
    cfg["overlay"] = {"min_confidence": "0.4"}
    cfg["ocr"] = {"gpu": "false"}

    c = AppController(cfg)
    yield c
    c._timer.stop()


# ── toggle_pause ────────────────────────────────────────────────────────

def test_toggle_pause_noop_when_not_active(controller):
    assert controller._active is False
    controller.toggle_pause()  # не должно упасть
    assert controller._user_paused is False


def test_toggle_pause_pauses_when_active(controller):
    controller._active = True
    controller._timer.start()
    assert controller._timer.isActive()

    controller.toggle_pause()
    assert controller._user_paused is True
    assert not controller._timer.isActive()


def test_toggle_pause_twice_resumes(controller):
    controller._active = True
    controller._timer.start()

    controller.toggle_pause()  # → paused
    controller.toggle_pause()  # → resumed
    assert controller._user_paused is False
    assert controller._timer.isActive()


# ── interaction with auto-pause (drag) ─────────────────────────────────

def test_resume_ignored_while_user_paused(controller):
    """Drag-resume не должен снимать явную пользовательскую паузу."""
    controller._active = True
    controller._timer.start()

    # Пользователь нажал паузу
    controller.toggle_pause()
    assert controller._user_paused is True
    assert not controller._timer.isActive()

    # Затем кто-то таскает рамку: pause() → resume() (от debounce)
    controller.pause()  # уже остановлен — no-op
    controller.resume()  # должен ИГНОРИРОВАТЬСЯ т.к. _user_paused
    assert not controller._timer.isActive()
    assert controller._user_paused is True


def test_drag_resume_works_when_not_user_paused(controller):
    controller._active = True
    controller._timer.start()
    controller.pause()  # auto-pause от drag
    assert not controller._timer.isActive()
    controller.resume()  # auto-resume от debounce
    assert controller._timer.isActive()


# ── stop сбрасывает user_paused ────────────────────────────────────────

def test_stop_clears_user_paused(controller):
    controller._active = True
    controller._timer.start()
    controller.toggle_pause()
    assert controller._user_paused is True

    controller.stop()
    assert controller._user_paused is False
    assert controller._active is False


# ── сигнал user_pause_changed ──────────────────────────────────────────

def test_toggle_pause_emits_signal(controller):
    controller._active = True
    controller._timer.start()

    received = []
    controller.user_pause_changed.connect(lambda v: received.append(v))

    controller.toggle_pause()
    controller.toggle_pause()
    assert received == [True, False]
