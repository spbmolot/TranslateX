"""
Валидатор config.ini.

Цель: при битом или ручке-правленном config.ini не падать молча с
ConfigParserError/ValueError где-то в недрах pipeline, а:
  • поймать все ошибки разом,
  • для каждой описать что не так и какое значение применили вместо неё,
  • вернуть список человекочитаемых сообщений (для лога и опц. QMessageBox).

Контракт:
    issues = validate_and_fix(config)
    # config мутирован in-place: невалидные ключи перезаписаны дефолтами.
    # issues — список строк (пусто = всё ок).

Дизайн:
  • Работает поверх уже применённых дефолтов (load_config()), поэтому мы
    проверяем только тип/диапазон/whitelist значений, а не наличие ключей.
  • Никаких raise — UI должен оставаться рабочим даже на полностью
    мусорном config.ini.
"""
from __future__ import annotations

import configparser
import logging
import re
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)

# Whitelist кодов языков (синхронизировано с ui/settings_dialog.LANGUAGES,
# но здесь — single-source-of-truth для валидации).
KNOWN_SOURCE_LANGS = {
    "auto", "en", "ru", "de", "fr", "es", "zh", "ja", "ko",
    "it", "pt", "nl", "pl", "ar", "tr", "uk",
}
KNOWN_TARGET_LANGS = KNOWN_SOURCE_LANGS - {"auto"}

# Регэксп для hotkey-строки (keyboard library): сегменты через '+',
# латиница/цифры/space, пробелы вокруг '+' допускаются.
_HOTKEY_RE = re.compile(r"^[a-z0-9+\-_ ]+$", re.IGNORECASE)

_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _check_int(
    config: configparser.ConfigParser,
    section: str,
    key: str,
    default: int,
    min_v: int,
    max_v: int,
    issues: List[str],
) -> None:
    raw = config.get(section, key, fallback=str(default))
    try:
        v = int(raw)
    except (TypeError, ValueError):
        issues.append(
            f"[{section}].{key} = {raw!r} не число — применено {default}."
        )
        config.set(section, key, str(default))
        return
    if not (min_v <= v <= max_v):
        issues.append(
            f"[{section}].{key} = {v} вне диапазона [{min_v}..{max_v}] "
            f"— применено {default}."
        )
        config.set(section, key, str(default))


def _check_float(
    config: configparser.ConfigParser,
    section: str,
    key: str,
    default: float,
    min_v: float,
    max_v: float,
    issues: List[str],
) -> None:
    raw = config.get(section, key, fallback=str(default))
    try:
        v = float(raw)
    except (TypeError, ValueError):
        issues.append(
            f"[{section}].{key} = {raw!r} не число — применено {default}."
        )
        config.set(section, key, str(default))
        return
    if not (min_v <= v <= max_v):
        issues.append(
            f"[{section}].{key} = {v} вне диапазона [{min_v}..{max_v}] "
            f"— применено {default}."
        )
        config.set(section, key, str(default))


def _check_bool(
    config: configparser.ConfigParser,
    section: str,
    key: str,
    default: bool,
    issues: List[str],
) -> None:
    raw = config.get(section, key, fallback=str(default).lower())
    try:
        # configparser.getboolean более тонко знает синонимы (true/yes/1/on)
        config.getboolean(section, key)
    except (ValueError, configparser.Error):
        issues.append(
            f"[{section}].{key} = {raw!r} не bool — применено {default}."
        )
        config.set(section, key, "true" if default else "false")


def _check_str(
    config: configparser.ConfigParser,
    section: str,
    key: str,
    default: str,
    issues: List[str],
    *,
    allowed: Optional[set] = None,
    pattern: Optional[re.Pattern] = None,
    label: Optional[str] = None,
) -> None:
    raw = (config.get(section, key, fallback=default) or "").strip()
    desc = label or f"[{section}].{key}"
    if not raw:
        issues.append(f"{desc} пустое — применено {default!r}.")
        config.set(section, key, default)
        return
    if allowed is not None and raw not in allowed:
        issues.append(
            f"{desc} = {raw!r} не в списке допустимых "
            f"({sorted(allowed)[:6]}…) — применено {default!r}."
        )
        config.set(section, key, default)
        return
    if pattern is not None and not pattern.match(raw):
        issues.append(f"{desc} = {raw!r} некорректный формат — применено {default!r}.")
        config.set(section, key, default)


def validate_and_fix(config: configparser.ConfigParser) -> List[str]:
    """
    Проверяет config in-place. Возвращает список найденных проблем
    (с уже применённой починкой). Пустой список = всё ок.

    Должен вызываться ПОСЛЕ load_config() (т.е. дефолты уже на месте).
    """
    issues: List[str] = []

    # ── [general] ─────────────────────────────────────────────
    _check_str(config, "general", "hotkey_select", "ctrl+shift+t",
               issues, pattern=_HOTKEY_RE, label="Горячая клавиша 'Выбор области'")
    _check_str(config, "general", "hotkey_toggle", "ctrl+shift+p",
               issues, pattern=_HOTKEY_RE, label="Горячая клавиша 'Вкл/выкл'")
    _check_str(config, "general", "hotkey_pause", "ctrl+shift+space",
               issues, pattern=_HOTKEY_RE, label="Горячая клавиша 'Пауза'")
    _check_str(config, "general", "hotkey_copy", "ctrl+shift+c",
               issues, pattern=_HOTKEY_RE, label="Горячая клавиша 'Копировать'")
    _check_str(config, "general", "hotkey_fullscreen", "ctrl+shift+f",
               issues, pattern=_HOTKEY_RE, label="Горячая клавиша 'Весь экран'")
    _check_int(config, "general", "capture_interval_ms", 1500, 100, 10000, issues)
    _check_int(config, "general", "cache_ttl_seconds",  3600, 0, 86400 * 30, issues)
    _check_int(config, "general", "cache_max_size",     500, 1, 100000, issues)

    # ── [languages] ───────────────────────────────────────────
    _check_str(config, "languages", "source_lang", "auto",
               issues, allowed=KNOWN_SOURCE_LANGS, label="Исходный язык")
    _check_str(config, "languages", "target_lang", "ru",
               issues, allowed=KNOWN_TARGET_LANGS, label="Язык перевода")

    # ── [translation] ─────────────────────────────────────────
    _check_str(config, "translation", "provider", "google",
               issues, allowed={"google", "deepl"}, label="Провайдер перевода")
    # deepl_api_key — пустой допустим (только если provider=google).
    if (config.get("translation", "provider", fallback="google") == "deepl"
            and not config.get("translation", "deepl_api_key", fallback="").strip()):
        issues.append(
            "Выбран провайдер DeepL, но deepl_api_key пуст. "
            "Будет использоваться Google Translate."
        )
        config.set("translation", "provider", "google")

    # ── [overlay] ─────────────────────────────────────────────
    _check_str  (config, "overlay", "style", "inpaint",
                 issues, allowed={"inpaint", "outline"}, label="Стиль overlay")
    _check_bool (config, "overlay", "exclude_from_capture", True, issues)
    _check_float(config, "overlay", "opacity", 0.0, 0.0, 1.0, issues)
    _check_int  (config, "overlay", "font_size", 13, 6, 72, issues)
    _check_bool (config, "overlay", "auto_font", True, issues)
    _check_str  (config, "overlay", "font_color", "#ffffff",
                 issues, pattern=_HEX_COLOR_RE, label="Цвет шрифта")
    _check_str  (config, "overlay", "bg_color", "#1a1a2e",
                 issues, pattern=_HEX_COLOR_RE, label="Цвет фона overlay")
    _check_float(config, "overlay", "min_confidence", 0.4, 0.0, 1.0, issues)

    # ── [ocr] ─────────────────────────────────────────────────
    _check_bool(config, "ocr", "gpu", False, issues)
    _check_bool(config, "ocr", "group_paragraphs", True, issues)
    # downscale: "auto" | "off" | float 0.1..1.0
    raw_ds = (config.get("ocr", "downscale", fallback="auto") or "").strip().lower()
    if raw_ds not in ("auto", "off"):
        try:
            v = float(raw_ds)
            if not (0.1 <= v < 1.0):
                raise ValueError
        except (TypeError, ValueError):
            issues.append(
                f"[ocr].downscale = {raw_ds!r} — ожидается auto/off/число 0.1..1.0 "
                "— применено 'auto'."
            )
            config.set("ocr", "downscale", "auto")

    # ── [ui] (опциональная секция темы — A2 + U3) ─────────────
    if config.has_section("ui"):
        _check_str(config, "ui", "theme", "auto",
                   issues, allowed={"auto", "light", "dark"}, label="Тема оформления")

    return issues


def log_issues(issues: List[str]) -> None:
    """Формат-помощник: вывалить issues в лог одним блоком."""
    if not issues:
        return
    logger.warning("Найдено %d проблем(ы) в config.ini:", len(issues))
    for i, msg in enumerate(issues, 1):
        logger.warning("  %d) %s", i, msg)
