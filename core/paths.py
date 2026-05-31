"""
Единые пути к пользовательским данным TranslateX.

Все ИЗМЕНЯЕМЫЕ данные (config.ini, лог, кэш переводов) живут в
%APPDATA%\\TranslateX, а НЕ рядом с .exe. Зачем:
  • обновление приложения не затирает настройки пользователя;
  • не требуются права администратора (Program Files доступен только на чтение);
  • все пользовательские данные собраны в одном месте.

Источник версии и неизменяемые ресурсы (модели, дефолтный config-шаблон)
по-прежнему лежат в дистрибутиве рядом с .exe.
"""
from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


def _base_dir() -> Path:
    """Каталог пользовательских данных (без создания на диске)."""
    base = os.environ.get("APPDATA")
    if base:
        return Path(base) / "TranslateX"
    return Path.home() / ".translatex"


def user_data_dir(create: bool = True) -> Path:
    d = _base_dir()
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def config_path() -> Path:
    return _base_dir() / "config.ini"


def log_path() -> Path:
    return _base_dir() / "translatex.log"


def cache_path() -> Path:
    return _base_dir() / "cache.json"


def bundled_default_config() -> Optional[Path]:
    """Дефолтный config.ini, вшитый в сборку (в dev — из корня репозитория)."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        p = Path(base) / "config.ini"
    else:
        p = Path(__file__).resolve().parent.parent / "config.ini"
    return p if p.exists() else None


def _legacy_config_candidates() -> List[Path]:
    """
    Где предыдущие версии TranslateX могли хранить пользовательский config.ini
    (рядом с .exe / внутри _internal). Используется для одноразовой миграции.
    """
    cands: List[Path] = []
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        cands.append(exe_dir / "config.ini")
        cands.append(exe_dir / "_internal" / "config.ini")
    return cands


def ensure_user_config() -> Path:
    """
    Гарантирует наличие config.ini в %APPDATA%\\TranslateX.

    Порядок:
      1. Уже есть — ничего не делаем.
      2. Найден config от прежней установки (рядом с .exe) — мигрируем его,
         чтобы не потерять настройки пользователя.
      3. Иначе — сидируем дефолтным шаблоном из дистрибутива.
    """
    target = config_path()
    if target.exists():
        return target

    user_data_dir()  # создать каталог

    for old in _legacy_config_candidates():
        try:
            if old.exists() and old.resolve() != target.resolve():
                shutil.copy2(old, target)
                logger.info(f"Перенёс config из прежней установки: {old}")
                return target
        except OSError as e:
            logger.warning(f"Не удалось мигрировать config из {old}: {e}")

    src = bundled_default_config()
    if src is not None:
        try:
            shutil.copy2(src, target)
            logger.info("Создал config.ini из дефолтного шаблона")
        except OSError as e:
            logger.warning(f"Не удалось создать config.ini из шаблона: {e}")
    return target
