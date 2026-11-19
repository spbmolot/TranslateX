"""Settings changes only reach the running pipeline after an atomic save."""
import configparser
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5.QtWidgets import QApplication, QMessageBox

from core import models
from ui import settings_dialog as settings


@pytest.fixture
def dialog(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    config = configparser.ConfigParser()
    config.read_dict({s: {} for s in ("general", "languages", "translation", "ocr", "overlay", "local", "ui")})
    config["translation"]["provider"] = "google"
    config["local"]["models_dir"] = str(tmp_path / "active")
    monkeypatch.setattr(settings.hardware, "detect_gpu", lambda: None)
    monkeypatch.setattr(settings, "user_data_dir", lambda: tmp_path)
    monkeypatch.setattr(settings, "config_path", lambda: tmp_path / "config.ini")
    monkeypatch.setattr(models, "_models_dir_override", Path(config["local"]["models_dir"]))
    d = settings.SettingsDialog(config)
    yield config, d
    d.close()


def test_failed_save_leaves_settings_open_and_old_config_in_force(dialog, tmp_path, monkeypatch):
    config, d = dialog
    d._models_dir.setText(str(tmp_path / "draft"))
    d._radio_local_fast.setChecked(True)
    components = d._required_components()
    assert components and components[0].directory().parent == tmp_path / "draft"
    assert models.models_dir() == tmp_path / "active"
    d._radio_google.setChecked(True)
    d._interval.setValue(3000)
    saved = []
    d.settings_saved.connect(saved.append)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *_: warnings.append(True))
    # Force a write failure even on Windows running with administrator rights.
    monkeypatch.setattr(settings, "config_path", lambda: tmp_path)
    d._save()
    assert warnings and not saved and d.result() == 0
    assert config["local"]["models_dir"] == str(tmp_path / "active")
    assert "capture_interval_ms" not in config["general"]
    monkeypatch.setattr(settings, "config_path", lambda: tmp_path / "config.ini")
    d._save()
    assert len(saved) == 1 and d.result() == 1
    assert saved[0].getint("general", "capture_interval_ms") == 3000
    assert "capture_interval_ms" not in config["general"]
    on_disk = configparser.ConfigParser()
    on_disk.read(tmp_path / "config.ini", encoding="utf-8")
    assert on_disk["local"]["models_dir"] == str(tmp_path / "draft")


def test_invalid_and_duplicate_shortcuts_and_missing_deepl_key_stay_editable(dialog, tmp_path, monkeypatch):
    _, d = dialog
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *_: warnings.append(True))
    d._hk_select.setText("bad+hotkey")
    d._save()
    d._hk_select.setText("ctrl+alt+f8")
    d._hk_copy.setText("alt+ctrl+F8")
    d._save()
    d._hk_copy.clear()
    d._radio_deepl.setChecked(True)
    d._deepl_key.clear()
    d._save()
    assert len(warnings) == 3 and d.result() == 0
    assert not (tmp_path / "config.ini").exists()
