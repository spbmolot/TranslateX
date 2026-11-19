"""
Локальный перевод: хранилище и загрузка моделей, выбор сборки под
видеокарту, быстрый движок, LLM-джоб (частичные результаты, прерывание),
очередь медленного провайдера в контроллере.
"""
from __future__ import annotations

import configparser
import hashlib
import http.server
import io
import os
import sys
import threading
import zipfile
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QRect
from PyQt5.QtWidgets import QApplication

import core.cache as cache_module
import core.pipeline as pipeline_module
from core import hardware, local_llm, local_mt, models
from core.cache import TranslationCache
from core.hardware import GpuInfo
from core.ocr import TextBlock
from core.pipeline import AppController
from core.translator import TranslationJobThread


@pytest.fixture
def models_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(models, "_models_dir_override", tmp_path / "models")
    return tmp_path / "models"


@pytest.fixture
def cache_file(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_module, "_CACHE_FILE", tmp_path / "cache.json")


# ── Загрузка ─────────────────────────────────────────────────────────────

PAYLOAD = bytes(range(256)) * 400  # 102 400 байт


class _Handler(http.server.BaseHTTPRequestHandler):
    files = {}
    range_requests = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        data = self.files.get(self.path)
        if data is None:
            self.send_error(404)
            return
        rng = self.headers.get("Range")
        start = 0
        if rng:
            start = int(rng.split("=")[1].split("-")[0])
            type(self).range_requests.append(start)
            self.send_response(206)
        else:
            self.send_response(200)
        body = data[start:]
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def http_server():
    _Handler.files = {}
    _Handler.range_requests = []
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_download_verifies_hash_and_reports_progress(http_server, tmp_path):
    _Handler.files["/f.bin"] = PAYLOAD
    seen = []
    dest = models.download_file(
        models.RemoteFile(http_server + "/f.bin", len(PAYLOAD), _sha(PAYLOAD)),
        tmp_path / "f.bin", lambda d, t: seen.append((d, t)))
    assert dest.read_bytes() == PAYLOAD
    assert seen[-1] == (len(PAYLOAD), len(PAYLOAD))
    assert not (tmp_path / "f.bin.part").exists()


def test_download_resumes_partial_file(http_server, tmp_path):
    _Handler.files["/f.bin"] = PAYLOAD
    (tmp_path / "f.bin.part").write_bytes(PAYLOAD[:30_000])
    dest = models.download_file(
        models.RemoteFile(http_server + "/f.bin", len(PAYLOAD), _sha(PAYLOAD)),
        tmp_path / "f.bin")
    assert dest.read_bytes() == PAYLOAD
    assert _Handler.range_requests == [30_000]  # докачали, а не начали заново


def test_download_rejects_corrupted_file(http_server, tmp_path):
    _Handler.files["/f.bin"] = PAYLOAD
    with pytest.raises(models.DownloadError, match="контрольная сумма"):
        models.download_file(
            models.RemoteFile(http_server + "/f.bin", len(PAYLOAD), "0" * 64),
            tmp_path / "f.bin")
    assert not (tmp_path / "f.bin").exists()
    assert not (tmp_path / "f.bin.part").exists()


def test_download_missing_file_is_clear_error(http_server, tmp_path):
    with pytest.raises(models.DownloadError, match="не найден"):
        models.download_file(
            models.RemoteFile(http_server + "/nope.zip", 1), tmp_path / "x")


def test_download_can_be_cancelled(http_server, tmp_path):
    _Handler.files["/f.bin"] = PAYLOAD
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(models.DownloadCancelled):
        models.download_file(
            models.RemoteFile(http_server + "/f.bin", len(PAYLOAD)),
            tmp_path / "f.bin", cancel=cancel)


def _zip(entries: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()


def test_install_unzips_and_strips_single_top_folder(http_server, models_dir):
    data = _zip({"build/llama-server.exe": b"exe", "build/lib/x.dll": b"dll"})
    _Handler.files["/rt.zip"] = data
    comp = models.Component(
        key="rt", title="rt", marker="llama-server.exe",
        files=[models.RemoteFile(http_server + "/rt.zip", len(data), _sha(data), True)])
    assert not comp.is_installed()
    models.install(comp)
    assert comp.is_installed()
    assert (models_dir / "rt" / "lib" / "x.dll").read_bytes() == b"dll"
    assert not (models_dir / "rt" / "rt.zip").exists()


def test_unzip_blocks_path_traversal(tmp_path):
    archive = tmp_path / "evil.zip"
    archive.write_bytes(_zip({"../../evil.txt": b"x", "ok.txt": b"y"}))
    with pytest.raises(models.DownloadError, match="небезопасный"):
        models._safe_extract(archive, tmp_path / "out")
    assert not (tmp_path.parent / "evil.txt").exists()


def test_models_dir_from_config(tmp_path):
    cfg = configparser.ConfigParser()
    cfg["local"] = {"models_dir": str(tmp_path / "m")}
    try:
        models.configure(cfg)
        assert models.models_dir() == tmp_path / "m"
        cfg["local"]["models_dir"] = ""
        models.configure(cfg)
        assert models.models_dir() == models.default_models_dir()
    finally:
        models._models_dir_override = None


# ── Выбор сборки под видеокарту ──────────────────────────────────────────

NVIDIA = GpuInfo("NVIDIA GeForce RTX 3060", "nvidia", 12288)
AMD = GpuInfo("AMD Radeon RX 6600", "amd", 8192)
WEAK = GpuInfo("AMD Radeon R7 240", "amd", 2048)


@pytest.mark.parametrize("device, gpu, expected", [
    ("auto", NVIDIA, ["cuda", "vulkan", "cpu"]),
    ("auto", AMD, ["vulkan", "cpu"]),
    ("auto", WEAK, ["cpu"]),              # мало памяти — видеокарту не трогаем
    ("auto", None, ["cpu"]),
    ("gpu", WEAK, ["vulkan", "cpu"]),     # принудительно, с откатом
    ("gpu", None, ["cpu"]),
    ("cpu", NVIDIA, ["cpu"]),
])
def test_llm_variants(device, gpu, expected):
    assert hardware.llm_variants(device, gpu) == expected


def test_llm_server_reports_missing_runtime(models_dir):
    with pytest.raises(local_mt.LocalModelMissing):
        local_llm.LlmServer()._candidates("cpu")


def test_llm_prompt_names_languages():
    prompt = local_llm.build_prompt("Hello", "en", "ru")
    assert "English (en) to Russian (ru)" in prompt
    assert prompt.endswith("Hello<end_of_turn>\n<start_of_turn>model\n")


# ── Быстрый движок ───────────────────────────────────────────────────────

@pytest.mark.parametrize("text, lang", [
    ("Hello world", "en"), ("Привет, мир", "ru"), ("こんにちは世界", "ja"),
    ("你好世界", "zh"), ("안녕하세요", "ko"), ("12345 !!!", "en"),
])
def test_guess_source_lang(text, lang):
    assert local_mt.guess_source_lang(text) == lang


def test_split_sentences():
    assert local_mt.split_sentences("Hey! What's up? Fine.") == ["Hey!", "What's up?", "Fine."]
    assert local_mt.split_sentences("No terminator") == ["No terminator"]
    assert local_mt.split_sentences("v1.2 is out") == ["v1.2 is out"]


class _FakePair:
    def __init__(self, src, tgt):
        self.tag = f"{src}>{tgt}"

    def translate(self, sentences):
        return [f"[{self.tag}]{s}" for s in sentences]


@pytest.fixture
def fake_pairs(monkeypatch):
    installed = set()
    monkeypatch.setattr(local_mt, "_model", lambda a, b: _FakePair(a, b))
    monkeypatch.setattr(
        local_mt.models, "opus_model",
        lambda a, b, root=None: SimpleNamespace(is_installed=lambda: (a, b) in installed))
    return installed


def test_fast_translate_per_sentence_and_skips_target_language(fake_pairs):
    fake_pairs.add(("en", "ru"))
    out = local_mt.translate(["Hi! Bye.", "Уже русский"], "auto", "ru")
    assert out == ["[en>ru]Hi! [en>ru]Bye.", "Уже русский"]


def test_fast_translate_pivots_through_english(fake_pairs):
    fake_pairs.update({("ja", "en"), ("en", "ru")})
    assert local_mt.translate(["こんにちは"], "ja", "ru") == ["[en>ru][ja>en]こんにちは"]


def test_fast_translate_missing_model(fake_pairs):
    with pytest.raises(local_mt.LocalModelMissing):
        local_mt.translate(["Hallo"], "de", "ru")


# ── Джоб перевода ────────────────────────────────────────────────────────

def _tb(text, y=0):
    return TextBlock(text=text,
                     bbox=[[10, y], [200, y], [200, y + 20], [10, y + 20]],
                     confidence=0.9)


def _job(blocks, provider):
    return TranslationJobThread(
        blocks=blocks, target_lang="ru", source_lang="en", provider=provider,
        deepl_api_key="", cache=TranslationCache(10, 3600))


def _run(job):
    final, partial = [], []
    job.translation_complete.connect(final.append)
    job.translation_partial.connect(partial.append)
    job.run()
    return final[0], partial


def test_job_local_fast_dispatch(cache_file, monkeypatch):
    monkeypatch.setattr(local_mt, "translate",
                        lambda texts, s, t: [x.upper() for x in texts])
    job = _job([_tb("a"), _tb("b")], "local_fast")
    result, _ = _run(job)
    assert [b.translated for b in result] == ["A", "B"]
    assert (job.attempted_count, job.failed_count) == (2, 0)


def test_job_missing_model_gives_actionable_error(cache_file, monkeypatch):
    def boom(*a):
        raise local_mt.LocalModelMissing("быстрая модель en→ru не установлена")
    monkeypatch.setattr(local_mt, "translate", boom)
    job = _job([_tb("a")], "local_fast")
    _run(job)
    assert job.failed_count == job.attempted_count == 1
    assert "Настройках" in job.error_text


class _FakeLlm:
    def __init__(self, fail_at=None):
        self.calls = []
        self.fail_at = fail_at

    def translate(self, text, src, tgt, device):
        self.calls.append(text)
        if self.fail_at == len(self.calls):
            raise RuntimeError("server died")
        return f"<{text}>"


def test_job_llm_emits_partial_results(cache_file, monkeypatch):
    fake = _FakeLlm()
    monkeypatch.setattr(local_llm, "server", lambda: fake)
    job = _job([_tb("a"), _tb("b"), _tb("c")], "local_llm")
    result, partial = _run(job)
    assert [len(p) for p in partial] == [1, 2, 3]   # overlay заполняется по блоку
    assert [b.translated for b in result] == ["<a>", "<b>", "<c>"]
    assert (job.attempted_count, job.failed_count) == (3, 0)


def test_job_llm_interruption_keeps_done_blocks_in_cache(cache_file, monkeypatch):
    fake = _FakeLlm()
    monkeypatch.setattr(local_llm, "server", lambda: fake)
    job = _job([_tb("a"), _tb("b"), _tb("c")], "local_llm")
    job.isInterruptionRequested = lambda: len(fake.calls) >= 1
    _run(job)
    assert job.interrupted is True
    assert fake.calls == ["a"]
    assert job._cache.get("a", "ru", "en", "local_llm") == "<a>"
    assert (job.attempted_count, job.failed_count) == (1, 0)


def test_job_llm_engine_failure_stops_batch(cache_file, monkeypatch):
    fake = _FakeLlm(fail_at=1)
    monkeypatch.setattr(local_llm, "server", lambda: fake)
    job = _job([_tb("a"), _tb("b")], "local_llm")
    _run(job)
    assert fake.calls == ["a"]                      # второй блок не мучаем
    assert job.failed_count == job.attempted_count == 1


# ── Контроллер: очередь медленного провайдера ────────────────────────────

@pytest.fixture(scope="session")
def qapp():
    yield QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def controller(qapp, monkeypatch, cache_file):
    monkeypatch.setattr(pipeline_module, "OcrWorker", MagicMock())
    monkeypatch.setattr(pipeline_module.local_llm, "server",
                        lambda: SimpleNamespace(is_running=lambda: True,
                                                stop=lambda: None))
    cfg = configparser.ConfigParser()
    cfg["general"] = {"capture_interval_ms": "500",
                      "cache_ttl_seconds": "3600", "cache_max_size": "100"}
    cfg["languages"] = {"source_lang": "en", "target_lang": "ru"}
    cfg["translation"] = {"provider": "local_llm", "deepl_api_key": ""}
    cfg["overlay"] = {"min_confidence": "0.4"}
    cfg["ocr"] = {"gpu": "false", "group_paragraphs": "false"}
    c = AppController(cfg)
    c._active = True
    c.set_region(QRect(0, 0, 500, 300))
    yield c
    c._timer.stop()
    c._retry_timer.stop()
    c._autosave_timer.stop()
    models._models_dir_override = None


def test_slow_provider_runs_one_job_and_queues_latest(controller, monkeypatch):
    jobs = []

    def fake_cls(**kw):
        job = MagicMock()
        job.kw = kw
        job.interrupted = False
        jobs.append(job)
        return job
    monkeypatch.setattr(pipeline_module, "TranslationJobThread", fake_cls)

    controller._on_ocr_complete([_tb("first text")])
    assert len(jobs) == 1

    controller._on_ocr_complete([_tb("first text")])       # тот же текст — ждём
    assert len(jobs) == 1

    controller._on_ocr_complete([_tb("second text")])      # новый — в очередь
    controller._on_ocr_complete([_tb("third text")])       # вытесняет второй
    assert len(jobs) == 1
    jobs[0].requestInterruption.assert_called()

    # первый джоб завершился прерванным → стартует самый свежий текст
    jobs[0].interrupted = True
    shown = []
    controller.translation_ready.connect(lambda b, r: shown.append(b))
    controller.sender = lambda: jobs[0]
    controller._on_translation_complete([])
    assert shown == []                                      # неполный результат не показан
    assert len(jobs) == 2
    assert [b.text for b in jobs[1].kw["blocks"]] == ["third text"]


def test_partial_results_reach_overlay_only_for_current_text(controller, monkeypatch):
    monkeypatch.setattr(pipeline_module, "TranslationJobThread", MagicMock())
    controller._on_ocr_complete([_tb("first text")])
    shown = []
    controller.translation_ready.connect(lambda b, r: shown.append(b))

    controller._on_translation_partial(["block"])
    assert shown == [["block"]]

    controller._on_ocr_complete([_tb("other text")])        # текст сменился
    controller._on_translation_partial(["stale"])
    assert shown == [["block"]]


def test_provider_change_forces_retranslation(controller):
    controller._last_ocr_signature = ("x",)
    cfg = configparser.ConfigParser()
    cfg.read_dict({s: dict(controller._config[s]) for s in controller._config.sections()})
    cfg["translation"]["provider"] = "local_fast"
    controller.reload_config(cfg)
    assert controller._last_ocr_signature is None
