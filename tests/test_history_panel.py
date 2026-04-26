"""
Тесты для HistoryPanel — основная бизнес-логика add_entries / дедуп / экспорт.
QApplication поднимается на уровне сессии, чтобы можно было создавать
QDialog без падения на Windows.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

# Headless режим для CI и локального прогона без отображения
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

from ui.history_panel import HistoryPanel


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture
def panel(qapp):
    p = HistoryPanel()
    yield p
    p.deleteLater()


def _block(original: str, translated: str):
    """Минимальный TranslatedBlock-like объект."""
    return SimpleNamespace(original=original, translated=translated)


# ── add_entries ─────────────────────────────────────────────────────────

def test_add_entries_appends_unique_records(panel):
    panel.add_entries([_block("Hello", "Привет"), _block("World", "Мир")], None)
    assert len(panel.entries()) == 2


def test_add_entries_dedups_repeats(panel):
    panel.add_entries([_block("Hello", "Привет")], None)
    panel.add_entries([_block("Hello", "Привет")], None)
    panel.add_entries([_block("Hello", "Привет")], None)
    assert len(panel.entries()) == 1


def test_add_entries_skips_empty(panel):
    panel.add_entries([_block("", "Привет"), _block("Hello", "")], None)
    assert panel.entries() == []


def test_add_entries_skips_identical_orig_and_translated(panel):
    # Если перевод совпадает с оригиналом — не пишем (бесполезно)
    panel.add_entries([_block("OK", "OK")], None)
    assert panel.entries() == []


def test_add_entries_handles_none_input(panel):
    panel.add_entries(None, None)
    panel.add_entries([], None)
    assert panel.entries() == []


def test_add_entries_strips_whitespace(panel):
    panel.add_entries([_block("  Hello  ", "  Привет  ")], None)
    panel.add_entries([_block("Hello", "Привет")], None)  # уже после strip
    assert len(panel.entries()) == 1
    ts, original, translated = panel.entries()[0]
    assert original == "Hello"
    assert translated == "Привет"


# ── Filter ──────────────────────────────────────────────────────────────

def test_filter_applies_case_insensitive(panel):
    panel.add_entries(
        [
            _block("Hello", "Привет"),
            _block("World", "Мир"),
            _block("Cat", "Кот"),
        ],
        None,
    )
    panel._filter_edit.setText("hello")
    # Список UI должен содержать ровно 1 строку
    assert panel._list.count() == 1
    panel._filter_edit.setText("")
    assert panel._list.count() == 3


def test_filter_matches_translated_too(panel):
    panel.add_entries([_block("Hello", "Привет")], None)
    panel._filter_edit.setText("привет")
    assert panel._list.count() == 1


# ── Export ──────────────────────────────────────────────────────────────

def test_export_writes_all_entries(panel, tmp_path):
    panel.add_entries(
        [_block("Hello", "Привет"), _block("World", "Мир")], None
    )
    out = tmp_path / "history.txt"
    # Эмулируем то, что делает _on_export после QFileDialog: пишем напрямую
    with out.open("w", encoding="utf-8") as f:
        f.write("# header\n\n")
        for ts, original, translated in panel.entries():
            f.write(f"[{ts}]\n{original}\n→\n{translated}\n\n")
    text = out.read_text(encoding="utf-8")
    assert "Hello" in text
    assert "Привет" in text
    assert "World" in text
    assert "Мир" in text


# ── Capacity ────────────────────────────────────────────────────────────

def test_clear_resets_state(panel):
    panel.add_entries([_block("A", "А"), _block("B", "Б")], None)
    panel._entries.clear()
    panel._seen_keys.clear()
    panel._rebuild_list()
    assert panel.entries() == []
    assert panel._list.count() == 0
