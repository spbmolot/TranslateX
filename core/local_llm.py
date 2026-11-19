"""
Локальный перевод малой LLM (TranslateGemma 4B) через llama.cpp.

llama-server запускается отдельным процессом на 127.0.0.1 и живёт, пока
выбран этот провайдер. Отдельный процесс, а не Python-биндинг, потому что:
  • сборку (CUDA / Vulkan / CPU) можно выбрать на машине пользователя,
    не таская все варианты в установщике;
  • падение движка не роняет приложение;
  • при сбое GPU-сборки автоматически откатываемся на процессор.
"""
from __future__ import annotations

import atexit
import ctypes
import logging
import os
import socket
import subprocess
import threading
import time
from typing import List, Optional

from core import hardware, models
from core.local_mt import LocalModelMissing, guess_source_lang
from core.paths import user_data_dir

logger = logging.getLogger(__name__)

_NO_WINDOW = 0x08000000
_START_TIMEOUT_S = 180       # загрузка 2,5 ГБ модели с медленного диска
_REQUEST_TIMEOUT_S = 300

LANG_NAMES = {
    "en": "English", "ru": "Russian", "de": "German", "fr": "French",
    "es": "Spanish", "zh": "Chinese", "ja": "Japanese", "ko": "Korean",
    "it": "Italian", "pt": "Portuguese", "nl": "Dutch", "pl": "Polish",
    "ar": "Arabic", "tr": "Turkish", "uk": "Ukrainian",
}

# Формат запроса TranslateGemma (как в chat-шаблоне модели).
_PROMPT = (
    "<start_of_turn>user\n"
    "You are a professional {src_name} ({src}) to {tgt_name} ({tgt}) translator. "
    "Your goal is to accurately convey the meaning and nuances of the original "
    "{src_name} text while adhering to {tgt_name} grammar, vocabulary, and "
    "cultural sensitivities.\nProduce only the {tgt_name} translation, without "
    "any additional explanations or commentary. Please translate the following "
    "{src_name} text into {tgt_name}:\n\n\n{text}<end_of_turn>\n"
    "<start_of_turn>model\n"
)


def build_prompt(text: str, src: str, tgt: str) -> str:
    return _PROMPT.format(
        text=text, src=src, tgt=tgt,
        src_name=LANG_NAMES.get(src, src), tgt_name=LANG_NAMES.get(tgt, tgt))


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _kill_with_parent(proc: subprocess.Popen) -> None:
    """Привязывает дочерний процесс к Job Object с KILL_ON_JOB_CLOSE:
    если приложение упадёт или его убьют, llama-server (гигабайты ОЗУ)
    не останется висеть сиротой."""
    if os.name != "nt":
        return
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = ctypes.c_void_p
        k32.SetInformationJobObject.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        k32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

        class _BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                        ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", ctypes.c_uint32),
                        ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", ctypes.c_uint32),
                        ("Affinity", ctypes.c_size_t),
                        ("PriorityClass", ctypes.c_uint32),
                        ("SchedulingClass", ctypes.c_uint32)]

        class _EXT(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", _BASIC),
                        ("IoInfo", ctypes.c_uint64 * 6),
                        ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        job = k32.CreateJobObjectW(None, None)
        info = _EXT()
        info.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
        k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
        k32.AssignProcessToJobObject(job, int(proc._handle))
        proc._tx_job = job  # держим handle живым, пока жив процесс
    except Exception as e:  # не критично — есть atexit
        logger.debug(f"Job object не создан: {e}")


class LlmServer:
    """Один процесс llama-server на приложение. Потокобезопасен: запросы
    идут по одному (CPU-генерация всё равно не параллелится с выгодой)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._closing = threading.Event()
        self._proc: Optional[subprocess.Popen] = None
        self._port = 0
        self._device = ""       # с какой настройкой запущен
        self.variant = ""       # фактическая сборка: cuda | vulkan | cpu
        self._log = None

    # ── запуск ────────────────────────────────────────────────────────

    def _candidates(self, device: str) -> List[str]:
        gpu = hardware.detect_gpu() if device != "cpu" else None
        order = hardware.llm_variants(device, gpu)
        installed = [v for v in order if models.llama_runtime(v).is_installed()]
        if not installed:
            raise LocalModelMissing("движок llama.cpp не установлен")
        return installed

    def _spawn(self, variant: str) -> bool:
        exe = models.llama_runtime(variant).directory() / "llama-server.exe"
        model = models.llm_model().directory() / models.LLM_FILE
        port = _free_port()
        cmd = [str(exe), "-m", str(model), "-c", "2048", "--host", "127.0.0.1",
               "--port", str(port), "--parallel", "1"]
        if variant == "cpu":
            # Физические ядра: гиперпотоки генерацию не ускоряют
            cmd += ["-t", str(max(1, min(16, (os.cpu_count() or 2) // 2)))]
        else:
            cmd += ["-ngl", "99"]  # все слои на видеокарту
        try:
            self._log = open(user_data_dir() / "llama-server.log", "w",
                             encoding="utf-8", errors="replace")
        except OSError:
            self._log = None
        logger.info(f"Запуск LLM ({variant}): порт {port}")
        proc = subprocess.Popen(
            cmd, stdout=self._log or subprocess.DEVNULL, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, cwd=str(exe.parent),
            creationflags=_NO_WINDOW if os.name == "nt" else 0)
        self._proc = proc  # also allow shutdown to interrupt model loading
        _kill_with_parent(proc)

        import requests
        deadline = time.monotonic() + _START_TIMEOUT_S
        while time.monotonic() < deadline:
            if self._closing.is_set():
                proc.kill()
                raise RuntimeError("Локальный перевод завершает работу")
            if proc.poll() is not None:
                logger.warning(f"llama-server ({variant}) завершился при запуске, "
                               f"код {proc.returncode}")
                return False
            try:
                if requests.get(f"http://127.0.0.1:{port}/health",
                                timeout=2).status_code == 200:
                    self._proc, self._port, self.variant = proc, port, variant
                    return True
            except requests.RequestException:
                pass
            time.sleep(0.4)
        proc.kill()
        logger.warning(f"llama-server ({variant}) не ответил за {_START_TIMEOUT_S} с")
        return False

    def _ensure(self, device: str) -> None:
        if self._proc is not None and self._proc.poll() is None \
                and self._device == device:
            return
        self._stop_locked()
        if not models.llm_model().is_installed():
            raise LocalModelMissing("модель LLM не скачана")
        for variant in self._candidates(device):
            if self._closing.is_set():
                raise RuntimeError("Локальный перевод завершает работу")
            if self._spawn(variant):
                self._device = device
                return
            logger.warning(f"Сборка {variant} не запустилась — пробуем следующую")
        raise RuntimeError("не удалось запустить локальную LLM (см. llama-server.log)")

    # ── перевод ───────────────────────────────────────────────────────

    def translate(self, text: str, source_lang: str, target_lang: str,
                  device: str = "auto") -> Optional[str]:
        import requests
        tgt = (target_lang or "ru").lower()[:2]
        src = (source_lang or "auto").lower()[:2]
        if src == "au":
            src = guess_source_lang(text)
        if src == tgt:
            return text
        with self._lock:
            if self._closing.is_set():
                raise RuntimeError("Локальный перевод завершает работу")
            self._ensure(device)
            r = requests.post(
                f"http://127.0.0.1:{self._port}/completion",
                json={"prompt": build_prompt(text, src, tgt),
                      "n_predict": max(64, min(512, len(text))),
                      "temperature": 0, "cache_prompt": True,
                      "stop": ["<end_of_turn>"]},
                timeout=_REQUEST_TIMEOUT_S)
            r.raise_for_status()
            out = (r.json().get("content") or "").strip()
            return out or None

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    # ── остановка ─────────────────────────────────────────────────────

    def _stop_locked(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(5)
            except subprocess.TimeoutExpired:
                proc.kill()
        if self._log is not None:
            try:
                self._log.close()
            except OSError:
                pass
            self._log = None
        self.variant = ""

    def begin_shutdown(self) -> None:
        """Interrupt local inference/startup without waiting on the request lock."""
        self._closing.set()
        proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.kill()

    def stop(self) -> None:
        """Остановить сервер. Не ждёт идущий запрос: убиваем процесс сразу,
        запрос завершится ошибкой соединения."""
        proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.kill()
        with self._lock:
            self._stop_locked()


_server = LlmServer()
atexit.register(_server.stop)


def server() -> LlmServer:
    return _server
