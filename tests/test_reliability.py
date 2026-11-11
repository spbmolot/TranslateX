"""
Надёжность: атомарный кэш с автосохранением, учёт сбоев перевода,
backoff-повтор без нового OCR, единственный экземпляр приложения.
"""
from __future__ import annotations

import configparser
import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QRect
from PyQt5.QtWidgets import QApplication

import core.cache as cache_module
import core.pipeline as pipeline_module
from core.cache import TranslationCache, normalize_text
from core.ocr import TextBlock
from core.pipeline import AppController
from core.translator import TranslationJobThread, _describe_error


# ── Кэш ──────────────────────────────────────────────────────────────────

@pytest.fixture
def cache_file(tmp_path, monkeypatch):
    path = tmp_path / "cache.json"
    monkeypatch.setattr(cache_module, "_CACHE_FILE", path)
    return path


def test_cache_save_is_atomic_and_reloadable(cache_file):
    cache = TranslationCache(max_size=10, ttl_seconds=3600)
    cache.put("hello", "ru", "привет")
    cache.save()

    assert cache_file.exists()
    assert not cache_file.with_name("cache.json.tmp").exists()
    json.loads(cache_file.read_text(encoding="utf-8"))  # валидный JSON

    assert TranslationCache(max_size=10, ttl_seconds=3600).get("hello", "ru") == "привет"


def test_cache_failed_save_keeps_previous_file(cache_file):
    cache = TranslationCache(max_size=10, ttl_seconds=3600)
    cache.put("hello", "ru", "привет")
    cache.save()
    before = cache_file.read_text(encoding="utf-8")

    cache.put("world", "ru", "мир")
    with patch("core.cache.json.dump", side_effect=OSError("disk full")):
        cache.save()

    assert cache_file.read_text(encoding="utf-8") == before
    assert cache.save_if_dirty() is True  # сбой не сбросил признак изменений


def test_cache_save_if_dirty_only_after_changes(cache_file):
    cache = TranslationCache(max_size=10, ttl_seconds=3600)
    assert cache.save_if_dirty() is False
    cache.put("hello", "ru", "привет")
    assert cache.save_if_dirty() is True
    assert cache.save_if_dirty() is False


def test_cache_configure_shrinks(cache_file):
    cache = TranslationCache(max_size=10, ttl_seconds=3600)
    for i in range(5):
        cache.put(f"t{i}", "ru", f"п{i}")
    cache.configure(max_size=2, ttl_seconds=3600)
    assert cache.get("t0", "ru") is None
    assert cache.get("t4", "ru") == "п4"


# ── Джоб перевода ────────────────────────────────────────────────────────

def _tb(text, y=0):
    return TextBlock(text=text,
                     bbox=[[10, y], [200, y], [200, y + 20], [10, y + 20]],
                     confidence=0.9)


def _job(blocks, cache, **kw):
    job = TranslationJobThread(
        blocks=blocks, target_lang="ru", source_lang="auto",
        provider="google", deepl_api_key="", cache=cache, **kw)
    out = []
    job.translation_complete.connect(out.append)
    job.run()
    return job, out[0]


@patch("core.translator.TranslationJobThread._translate_google")
def test_job_reports_failure_counts(mock_translate, cache_file):
    mock_translate.return_value = [None, "мир"]
    job, result = _job([_tb("hello"), _tb("world")], TranslationCache(10, 3600))
    assert (job.attempted_count, job.failed_count) == (2, 1)
    assert [b.translated for b in result] == ["hello", "мир"]


@patch("core.translator.TranslationJobThread._translate_google")
def test_job_cache_only_skips_network(mock_translate, cache_file):
    cache = TranslationCache(10, 3600)
    cache.put("hello", "ru", "привет")
    job, result = _job([_tb("hello"), _tb("world")], cache, cache_only=True)
    mock_translate.assert_not_called()
    assert job.attempted_count == 0
    assert [b.translated for b in result] == ["привет", "world"]


def test_describe_error_is_human_readable():
    assert "нет связи" in _describe_error(ConnectionError("Max retries exceeded"))
    assert "частоту" in _describe_error(Exception("429 Too Many Requests"))

    class AuthorizationException(Exception):
        pass
    assert "ключ" in _describe_error(AuthorizationException("forbidden"))


# ── Контроллер: backoff-повтор ───────────────────────────────────────────

@pytest.fixture(scope="session")
def qapp():
    yield QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def controller(qapp, monkeypatch, cache_file):
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
    c._retry_timer.stop()
    c._autosave_timer.stop()


def _result(blocks, attempted, failed, error=""):
    return SimpleNamespace(source_blocks=blocks, attempted_count=attempted,
                           failed_count=failed, error_text=error)


def test_total_failure_schedules_backoff_and_notifies_once(controller):
    blocks = [_tb("hello")]
    failures, statuses = [], []
    controller.translation_failed.connect(failures.append)
    controller.status_message.connect(statuses.append)

    controller._track_translation_health(_result(blocks, 1, 1, "нет связи"))
    assert controller._retry_timer.isActive()
    assert controller._retry_timer.interval() == 2000
    assert controller._retry_blocks is blocks
    assert failures == ["нет связи"]
    assert "Повтор через 2 с" in statuses[-1]

    controller._track_translation_health(_result(blocks, 1, 1, "нет связи"))
    assert controller._retry_timer.interval() == 4000
    assert failures == ["нет связи"]  # уведомление один раз на серию


def test_no_parallel_network_job_while_retry_in_flight(controller, monkeypatch):
    """Регрессия: таймер повтора уже сработал (неактивен), ответ ещё не
    пришёл — новый текст не должен уходить в сеть вторым запросом."""
    fake_job_cls = MagicMock()
    monkeypatch.setattr(pipeline_module, "TranslationJobThread", fake_job_cls)
    controller._track_translation_health(_result([_tb("old")], 1, 1))
    controller._retry_timer.stop()  # повторный джоб «в полёте»

    controller._on_ocr_complete([_tb("newer text")])
    assert fake_job_cls.call_args.kwargs["cache_only"] is True

    # повтор старого текста тоже упал — дальше повторяем уже свежий текст
    controller._track_translation_health(_result([_tb("old")], 1, 1))
    assert [b.text for b in controller._retry_blocks] == ["newer text"]


def test_recovery_translates_text_that_arrived_during_backoff(controller, monkeypatch):
    fake_job_cls = MagicMock()
    monkeypatch.setattr(pipeline_module, "TranslationJobThread", fake_job_cls)
    old = [_tb("old")]
    controller._track_translation_health(_result(old, 1, 1))
    controller._on_ocr_complete([_tb("newer text")])
    fake_job_cls.reset_mock()

    controller._track_translation_health(_result(old, 1, 0))

    assert controller._trans_fail_streak == 0
    kwargs = fake_job_cls.call_args.kwargs
    assert [b.text for b in kwargs["blocks"]] == ["newer text"]
    assert kwargs["cache_only"] is False


def test_backoff_delay_is_capped(controller):
    for _ in range(10):
        controller._track_translation_health(_result([_tb("a")], 1, 1))
    assert controller._retry_timer.interval() == controller._RETRY_MAX_DELAY_S * 1000


def test_partial_success_recovers(controller):
    recovered = []
    controller.translation_recovered.connect(lambda: recovered.append(True))
    controller._track_translation_health(_result([_tb("a")], 1, 1))
    controller._track_translation_health(_result([_tb("a"), _tb("b")], 2, 1))
    assert recovered == [True]
    assert controller._trans_fail_streak == 0
    assert not controller._retry_timer.isActive()


def test_cache_only_job_does_not_change_health(controller):
    controller._track_translation_health(_result([_tb("a")], 1, 1))
    controller._track_translation_health(_result([_tb("a")], 0, 0))
    assert controller._trans_fail_streak == 1
    assert controller._retry_timer.isActive()


def test_retry_restarts_translation_without_ocr(controller, monkeypatch):
    fake_job_cls = MagicMock()
    monkeypatch.setattr(pipeline_module, "TranslationJobThread", fake_job_cls)
    blocks = [_tb("hello")]
    controller._retry_blocks = blocks
    controller._last_ocr_signature = (normalize_text("hello"),)

    controller._retry_translation()

    assert fake_job_cls.call_count == 1
    kwargs = fake_job_cls.call_args.kwargs
    assert kwargs["blocks"] is blocks and kwargs["cache_only"] is False


def test_retry_skipped_when_screen_text_changed(controller, monkeypatch):
    fake_job_cls = MagicMock()
    monkeypatch.setattr(pipeline_module, "TranslationJobThread", fake_job_cls)
    controller._retry_blocks = [_tb("hello")]
    controller._last_ocr_signature = (normalize_text("other"),)
    controller._trans_fail_streak = 3
    controller._retry_translation()
    fake_job_cls.assert_not_called()
    assert controller._trans_fail_streak == 0  # backoff снят, цикл не залипает


def test_new_text_during_backoff_uses_cache_only(controller, monkeypatch):
    fake_job_cls = MagicMock()
    monkeypatch.setattr(pipeline_module, "TranslationJobThread", fake_job_cls)
    controller._track_translation_health(_result([_tb("old")], 1, 1))

    controller._on_ocr_complete([_tb("brand new text")])

    assert fake_job_cls.call_args.kwargs["cache_only"] is True
    assert [b.text for b in controller._retry_blocks] == ["brand new text"]


def test_stop_cancels_retry(controller):
    controller._track_translation_health(_result([_tb("a")], 1, 1))
    controller.stop()
    assert not controller._retry_timer.isActive()
    assert controller._trans_fail_streak == 0


def test_capture_failure_reschedules_tick(controller, monkeypatch):
    worker = MagicMock()
    worker._reader = object()
    controller._ocr_worker = worker
    monkeypatch.setattr(pipeline_module, "capture_to_numpy",
                        MagicMock(side_effect=OSError("grab failed")))
    controller._timer.stop()
    controller._on_timer_tick()
    assert controller._timer.isActive()


# ── Единственный экземпляр ───────────────────────────────────────────────

@pytest.mark.skipif(sys.platform != "win32", reason="Win32 mutex")
def test_single_instance_lock():
    from utils.win32_utils import _kernel32, acquire_single_instance
    name = f"TranslateX.test.{os.getpid()}"
    first = acquire_single_instance(name)
    assert first
    assert acquire_single_instance(name) is None
    _kernel32.CloseHandle(first)
    again = acquire_single_instance(name)
    assert again
    _kernel32.CloseHandle(again)
