"""
Регрессия для A2 (config-валидатор):
  • валидный конфиг → пустой список issues, ничего не меняется;
  • битый int / float / bool / hex-color / язык / hotkey → авто-фикс
    + понятная строка в issues;
  • DeepL без api_key → fallback на Google.
"""
from __future__ import annotations

import configparser

import pytest

from core.config_validator import validate_and_fix


def _make_config(overrides: dict[str, dict[str, str]] | None = None) -> configparser.ConfigParser:
    """Базовый валидный конфиг (как после load_config с дефолтами)."""
    c = configparser.ConfigParser()
    c.read_dict({
        "general": {
            "hotkey_select": "ctrl+shift+t",
            "hotkey_toggle": "ctrl+shift+p",
            "hotkey_pause":  "ctrl+shift+space",
            "capture_interval_ms": "1500",
            "cache_ttl_seconds":   "3600",
            "cache_max_size":      "500",
        },
        "languages": {"source_lang": "auto", "target_lang": "ru"},
        "translation": {"provider": "google", "deepl_api_key": ""},
        "overlay": {
            "opacity": "0.0", "font_size": "13", "auto_font": "true",
            "font_color": "#ffffff", "bg_color": "#1a1a2e",
            "min_confidence": "0.4",
        },
        "ocr": {"gpu": "false"},
    })
    if overrides:
        for section, kvs in overrides.items():
            if not c.has_section(section):
                c.add_section(section)
            for k, v in kvs.items():
                c.set(section, k, v)
    return c


def test_valid_config_no_issues():
    c = _make_config()
    issues = validate_and_fix(c)
    assert issues == []


def test_invalid_int_replaced_with_default():
    c = _make_config({"general": {"capture_interval_ms": "abc"}})
    issues = validate_and_fix(c)
    assert any("capture_interval_ms" in m for m in issues)
    assert c.getint("general", "capture_interval_ms") == 1500


def test_int_out_of_range_replaced():
    c = _make_config({"general": {"capture_interval_ms": "50"}})  # < 100
    issues = validate_and_fix(c)
    assert any("capture_interval_ms" in m and "50" in m for m in issues)
    assert c.getint("general", "capture_interval_ms") == 1500


def test_invalid_float_opacity_replaced():
    c = _make_config({"overlay": {"opacity": "1.5"}})  # > 1.0
    issues = validate_and_fix(c)
    assert any("opacity" in m for m in issues)
    assert c.getfloat("overlay", "opacity") == 0.0


def test_invalid_bool_gpu_replaced():
    c = _make_config({"ocr": {"gpu": "maybe"}})
    issues = validate_and_fix(c)
    assert any("gpu" in m for m in issues)
    assert c.getboolean("ocr", "gpu") is False


def test_unknown_target_lang_replaced():
    c = _make_config({"languages": {"target_lang": "klingon"}})
    issues = validate_and_fix(c)
    assert any("target" in m.lower() or "klingon" in m for m in issues)
    assert c.get("languages", "target_lang") == "ru"


def test_empty_hotkey_replaced():
    c = _make_config({"general": {"hotkey_select": "   "}})
    issues = validate_and_fix(c)
    assert any("hotkey_select" in m.lower() or "выбор области" in m.lower() for m in issues)
    assert c.get("general", "hotkey_select") == "ctrl+shift+t"


def test_invalid_hotkey_pattern_replaced():
    c = _make_config({"general": {"hotkey_pause": "ctrl+@#$"}})
    issues = validate_and_fix(c)
    assert any("пауза" in m.lower() or "hotkey_pause" in m.lower() for m in issues)
    assert c.get("general", "hotkey_pause") == "ctrl+shift+space"


def test_invalid_hex_color_replaced():
    c = _make_config({"overlay": {"font_color": "red"}})
    issues = validate_and_fix(c)
    assert any("шрифт" in m.lower() or "font_color" in m.lower() for m in issues)
    assert c.get("overlay", "font_color") == "#ffffff"


def test_deepl_without_key_falls_back_to_google():
    c = _make_config({"translation": {"provider": "deepl", "deepl_api_key": ""}})
    issues = validate_and_fix(c)
    assert any("DeepL" in m for m in issues)
    assert c.get("translation", "provider") == "google"


def test_unknown_provider_replaced():
    c = _make_config({"translation": {"provider": "yandex"}})
    issues = validate_and_fix(c)
    assert any("yandex" in m for m in issues)
    assert c.get("translation", "provider") == "google"


def test_validator_idempotent():
    """Повторный вызов validate_and_fix на уже исправленном config'е → 0 issues."""
    c = _make_config({"general": {"capture_interval_ms": "abc"}})
    validate_and_fix(c)
    issues2 = validate_and_fix(c)
    assert issues2 == []


def test_optional_ui_theme_validated_when_present():
    c = _make_config()
    c.add_section("ui")
    c.set("ui", "theme", "neon")  # не из whitelist
    issues = validate_and_fix(c)
    assert any("theme" in m.lower() or "тема" in m.lower() for m in issues)
    assert c.get("ui", "theme") == "auto"
