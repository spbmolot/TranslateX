"""
Регрессия для A1 (single-source-of-truth для версии):
  • parse_version корректно разбирает строку.
  • __version__ ↔ __version_tuple__ согласованы.
  • version_info.txt содержит актуальный FileVersion (если файл сгенерирован).

Если этот тест падает после bump'а версии — запусти:
    python scripts/sync_version.py
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.__version__ import __version__, __version_tuple__, parse_version


ROOT = Path(__file__).resolve().parent.parent


def test_parse_version_basic():
    assert parse_version("1.2.3") == (1, 2, 3, 0)


def test_parse_version_four_parts():
    assert parse_version("2.5.7.42") == (2, 5, 7, 42)


def test_parse_version_with_extra_parts_truncates():
    assert parse_version("1.2.3.4.5") == (1, 2, 3, 4)


def test_parse_version_with_non_numeric_skips():
    # "1.0.0-rc1" → числовые компоненты только "1","0","0"
    assert parse_version("1.0.0-rc1") == (1, 0, 0, 0)


def test_string_and_tuple_consistent():
    assert parse_version(__version__) == __version_tuple__


def test_version_info_txt_in_sync():
    """version_info.txt должен содержать текущий __version__ как ProductVersion."""
    p = ROOT / "version_info.txt"
    if not p.exists():
        pytest.skip("version_info.txt отсутствует — запусти scripts/sync_version.py")
    text = p.read_text(encoding="utf-8")
    assert f"u'{__version__}'" in text, (
        f"version_info.txt вне синхронизации с core/__version__.py "
        f"({__version__!r}). Запусти: python scripts/sync_version.py"
    )
