"""
Локальные модели перевода: где лежат, что установлено, как скачать.

Компоненты (каждый скачивается по требованию, в установщик не входит):
  • opus-mt-<src>-<tgt>  — быстрая модель перевода (CTranslate2), ~80 МБ на пару;
  • llm-model            — TranslateGemma 4B (GGUF), ~2,5 ГБ;
  • llama-<variant>      — исполняемые файлы llama.cpp: cpu / vulkan / cuda.

Каталог моделей — [local].models_dir в config.ini; по умолчанию
%LOCALAPPDATA%\\TranslateX\\models (большие файлы не должны попадать в
перемещаемый профиль). Пользователь может перенести его на другой диск.
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import threading
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)

# ── Источники ────────────────────────────────────────────────────────────
# Версия llama.cpp закреплена: обновление — осознанное действие с заменой
# контрольных сумм, а не «что сегодня лежит в latest».
LLAMA_TAG = "b12262"
_LLAMA_URL = f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_TAG}/"

LLM_FILE = "translategemma-4b-it-Q4_K_M.gguf"
_LLM_URL = ("https://huggingface.co/bullerwins/translategemma-4b-it-GGUF/"
            f"resolve/main/{LLM_FILE}")

# Сконвертированные OPUS-MT (scripts/convert_opus_mt.py) публикуются
# в релизах проекта. {pair} = "en-ru".
OPUS_URL_TEMPLATE = ("https://github.com/spbmolot/TranslateX/releases/download/"
                     "models-v1/opus-mt-{pair}-ct2-int8.zip")

ProgressCb = Callable[[int, int], None]  # (скачано байт, всего байт; 0 = неизвестно)


class DownloadCancelled(Exception):
    pass


class DownloadError(Exception):
    pass


@dataclass(frozen=True)
class RemoteFile:
    url: str
    size: int                      # ожидаемый размер, байт (для прогресса)
    sha256: Optional[str] = None   # None — не проверяем (источник без хэша)
    unzip: bool = False            # распаковать в каталог компонента


@dataclass(frozen=True)
class Component:
    key: str                       # он же имя подкаталога в models_dir
    title: str
    files: List[RemoteFile]
    marker: str                    # файл, наличие которого = «установлено»
    extra_markers: List[str] = field(default_factory=list)
    root: Optional[Path] = None

    @property
    def size(self) -> int:
        return sum(f.size for f in self.files)

    def directory(self) -> Path:
        return (self.root or models_dir()) / self.key

    def is_installed(self) -> bool:
        d = self.directory()
        return all((d / m).is_file() for m in [self.marker, *self.extra_markers])


# ── Каталог моделей ──────────────────────────────────────────────────────

_models_dir_override: Optional[Path] = None


def default_models_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if base:
        return Path(base) / "TranslateX" / "models"
    return Path.home() / ".translatex" / "models"


def configure(config) -> None:
    """Применить [local].models_dir из конфига (пусто = по умолчанию)."""
    global _models_dir_override
    raw = ""
    try:
        raw = (config.get("local", "models_dir", fallback="") or "").strip()
    except Exception:
        pass
    _models_dir_override = Path(raw) if raw else None


def models_dir() -> Path:
    return _models_dir_override or default_models_dir()


# ── Реестр компонентов ───────────────────────────────────────────────────

def llm_model(root: Optional[Path] = None) -> Component:
    return Component(
        key="llm", root=root,
        title="Модель TranslateGemma 4B",
        files=[RemoteFile(
            _LLM_URL, 2_489_909_312,
            "7f7357c14abd9da4eb200b38b05da502cd6e10d7e1d403fbc9f78c19f3209b72")],
        marker=LLM_FILE,
    )


_LLAMA_VARIANTS = {
    "cpu": ("Движок llama.cpp (процессор)", [
        RemoteFile(_LLAMA_URL + f"llama-{LLAMA_TAG}-bin-win-cpu-x64.zip", 25_000_000,
                   "7d28975f1acc6c142954c4a0d1dfb9c3a3a13736c41f898890748cc067487d7a", True),
    ]),
    "vulkan": ("Движок llama.cpp (видеокарта, Vulkan)", [
        RemoteFile(_LLAMA_URL + f"llama-{LLAMA_TAG}-bin-win-vulkan-x64.zip", 38_000_000,
                   "07717635401eb384ef640c0ebd7d8d2abd17d73e69023930d62149d2d528344a", True),
    ]),
    "cuda": ("Движок llama.cpp (видеокарта NVIDIA, CUDA)", [
        RemoteFile(_LLAMA_URL + f"llama-{LLAMA_TAG}-bin-win-cuda-12.4-x64.zip", 289_000_000,
                   "c8441b750db63f95c7311e974313cdd0293ea6f8bea8bf16ca888657c4bb0e62", True),
        RemoteFile(_LLAMA_URL + "cudart-llama-bin-win-cuda-12.4-x64.zip", 391_000_000,
                   "8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6", True),
    ]),
}


def llama_runtime(variant: str, root: Optional[Path] = None) -> Component:
    title, files = _LLAMA_VARIANTS[variant]
    return Component(key=f"llama-{LLAMA_TAG}-{variant}", title=title,
                     files=files, marker="llama-server.exe", root=root)


def opus_model(src: str, tgt: str, root: Optional[Path] = None) -> Component:
    pair = f"{src}-{tgt}"
    return Component(
        key=f"opus-mt-{pair}", root=root,
        title=f"Быстрая модель {src}→{tgt}",
        files=[RemoteFile(OPUS_URL_TEMPLATE.format(pair=pair), 80_000_000, None, True)],
        marker="model.bin",
        extra_markers=["source.spm", "target.spm"],
    )


def format_size(n: int) -> str:
    if n >= 1_000_000_000:
        return f"{n / 1e9:.1f} ГБ".replace(".", ",")
    return f"{max(1, round(n / 1e6))} МБ"


# ── Загрузка ─────────────────────────────────────────────────────────────

def _sha256(path: Path, cancel: Optional[threading.Event]) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            if cancel is not None and cancel.is_set():
                raise DownloadCancelled()
            chunk = f.read(4 * 1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def download_file(
    remote: RemoteFile,
    dest: Path,
    progress: Optional[ProgressCb] = None,
    cancel: Optional[threading.Event] = None,
) -> Path:
    """
    Скачивает файл с докачкой (<dest>.part + HTTP Range), проверкой SHA-256
    и атомарной подменой. Прерванную загрузку можно продолжить позже.
    """
    import requests

    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    have = part.stat().st_size if part.exists() else 0
    headers = {"Range": f"bytes={have}-"} if have else {}
    try:
        with requests.get(remote.url, stream=True, headers=headers, timeout=30) as r:
            if r.status_code == 416:      # .part уже полный
                pass
            elif r.status_code == 404:
                raise DownloadError("файл не найден на сервере (ещё не опубликован?)")
            else:
                r.raise_for_status()
                if have and r.status_code != 206:
                    have = 0              # сервер не умеет докачку — с нуля
                total = have + int(r.headers.get("Content-Length") or 0)
                with open(part, "ab" if have else "wb") as f:
                    for chunk in r.iter_content(chunk_size=1024 * 1024):
                        if cancel is not None and cancel.is_set():
                            raise DownloadCancelled()
                        f.write(chunk)
                        have += len(chunk)
                        if progress:
                            progress(have, total or remote.size)
    except requests.RequestException as e:
        raise DownloadError(f"ошибка сети: {e}") from e

    if remote.sha256:
        actual = _sha256(part, cancel)
        if actual != remote.sha256:
            part.unlink(missing_ok=True)
            raise DownloadError("файл повреждён (контрольная сумма не совпала), "
                                "повторите загрузку")
    os.replace(part, dest)
    return dest


def _safe_extract(zip_path: Path, target: Path) -> None:
    """Распаковка с защитой от выхода за пределы каталога (zip slip).
    Если всё содержимое лежит в одной вложенной папке — она «снимается»."""
    target = target.resolve()
    with zipfile.ZipFile(zip_path) as z:
        names = [n for n in z.namelist() if not n.endswith("/")]
        tops = {n.split("/", 1)[0] for n in names}
        strip = (len(tops) == 1 and all("/" in n for n in names))
        for name in names:
            rel = name.split("/", 1)[1] if strip else name
            out = (target / rel).resolve()
            if not str(out).startswith(str(target)):
                raise DownloadError(f"небезопасный путь в архиве: {name}")
            out.parent.mkdir(parents=True, exist_ok=True)
            with z.open(name) as src, open(out, "wb") as dst:
                shutil.copyfileobj(src, dst)


def install(
    component: Component,
    progress: Optional[ProgressCb] = None,
    cancel: Optional[threading.Event] = None,
) -> None:
    """Скачать и установить компонент. Прогресс — суммарный по всем файлам."""
    if component.is_installed():
        return
    target = component.directory()
    target.mkdir(parents=True, exist_ok=True)
    total = component.size
    done_before = 0
    for remote in component.files:
        name = remote.url.rsplit("/", 1)[1]
        dest = target / name

        def _cb(done: int, _file_total: int, _base=done_before) -> None:
            if progress:
                progress(min(_base + done, total), total)

        download_file(remote, dest, _cb, cancel)
        if remote.unzip:
            _safe_extract(dest, target)
            dest.unlink(missing_ok=True)
        done_before += remote.size
    if not component.is_installed():
        raise DownloadError("после установки не найдены ожидаемые файлы")
    logger.info(f"Установлен компонент {component.key} → {target}")


def remove(component: Component) -> None:
    shutil.rmtree(component.directory(), ignore_errors=True)
