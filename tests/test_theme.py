"""
Регрессия для U3 (темы оформления).

Тестируем без QApplication где можно (resolve_mode), и с offscreen-QApp
для apply_theme.
"""
from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

from ui.theme import (
    DARK_QSS, LIGHT_QSS, _detect_system_dark, apply_theme, resolve_mode,
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def test_resolve_mode_explicit_light():
    assert resolve_mode("light") == "light"


def test_resolve_mode_explicit_dark():
    assert resolve_mode("dark") == "dark"


def test_resolve_mode_unknown_falls_back_to_light(caplog):
    with caplog.at_level("WARNING"):
        assert resolve_mode("neon") == "light"  # type: ignore[arg-type]


def test_resolve_mode_auto_returns_valid_value():
    """auto должен резолвиться в light или dark — без падений."""
    v = resolve_mode("auto")
    assert v in ("light", "dark")


def test_detect_system_dark_returns_bool():
    """На любой ОС не должен бросать исключений и должен вернуть bool."""
    v = _detect_system_dark()
    assert isinstance(v, bool)


def test_apply_theme_light_sets_qss(qapp):
    actual = apply_theme(qapp, "light")
    assert actual == "light"
    assert qapp.styleSheet() == LIGHT_QSS


def test_apply_theme_dark_sets_qss(qapp):
    actual = apply_theme(qapp, "dark")
    assert actual == "dark"
    assert qapp.styleSheet() == DARK_QSS


def test_apply_theme_auto_returns_light_or_dark(qapp):
    actual = apply_theme(qapp, "auto")
    assert actual in ("light", "dark")
    assert qapp.styleSheet() in (LIGHT_QSS, DARK_QSS)


def test_apply_theme_palette_changed(qapp):
    apply_theme(qapp, "light")
    light_window = qapp.palette().color(qapp.palette().Window).name()
    apply_theme(qapp, "dark")
    dark_window = qapp.palette().color(qapp.palette().Window).name()
    assert light_window != dark_window
