"""
Диалог настроек — компактный, разбит на вкладки (QTabWidget).
Вкладки: Общее · Языки · Перевод · OCR · Отображение.
"""
import configparser
import logging
from pathlib import Path

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QRadioButton, QSpinBox,
    QTabWidget, QVBoxLayout, QWidget,
)

from core.__version__ import __version__

logger = logging.getLogger(__name__)

LANGUAGES = [
    ("auto", "Авто-определение"),
    ("en", "Английский"),
    ("ru", "Русский"),
    ("de", "Немецкий"),
    ("fr", "Французский"),
    ("es", "Испанский"),
    ("zh", "Китайский"),
    ("ja", "Японский"),
    ("ko", "Корейский"),
    ("it", "Итальянский"),
    ("pt", "Португальский"),
    ("nl", "Нидерландский"),
    ("pl", "Польский"),
    ("ar", "Арабский"),
    ("tr", "Турецкий"),
    ("uk", "Украинский"),
]


class SettingsDialog(QDialog):
    settings_saved = pyqtSignal(configparser.ConfigParser)

    def __init__(self, config: configparser.ConfigParser, parent=None):
        super().__init__(parent)
        self._config = config
        self.setWindowTitle(f"TranslateX v{__version__} — Настройки")
        self.setMinimumWidth(480)
        self.setMinimumHeight(420)
        self._build_ui()
        self._load_values()

    # -------- UI --------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_tab_general(), "Общее")
        self._tabs.addTab(self._build_tab_languages(), "Языки")
        self._tabs.addTab(self._build_tab_provider(), "Перевод")
        self._tabs.addTab(self._build_tab_ocr(), "OCR")
        self._tabs.addTab(self._build_tab_overlay(), "Отображение")
        root.addWidget(self._tabs)

        buttons = QDialogButtonBox()
        buttons.addButton("Сохранить", QDialogButtonBox.AcceptRole)
        buttons.addButton("Отмена", QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _build_tab_general(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)

        self._hk_select = QLineEdit()
        self._hk_select.setPlaceholderText("напр. ctrl+shift+t")
        form.addRow("Горячая клавиша — выбор области:", self._hk_select)

        self._hk_toggle = QLineEdit()
        self._hk_toggle.setPlaceholderText("напр. ctrl+shift+p")
        form.addRow("Горячая клавиша — вкл/выкл перевод:", self._hk_toggle)

        self._interval = QSpinBox()
        self._interval.setRange(500, 10000)
        self._interval.setSingleStep(100)
        self._interval.setSuffix(" мс")
        form.addRow("Интервал захвата:", self._interval)

        note = QLabel(
            "<small><i>Меньше интервал → быстрее реакция, но выше нагрузка на CPU/GPU.</i></small>"
        )
        note.setWordWrap(True)
        form.addRow(note)
        return w

    def _build_tab_languages(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        self._source_lang = QComboBox()
        self._target_lang = QComboBox()
        for code, name in LANGUAGES:
            self._source_lang.addItem(name, code)
            if code != "auto":
                self._target_lang.addItem(name, code)
        form.addRow("Исходный язык:", self._source_lang)
        form.addRow("Язык перевода:", self._target_lang)
        return w

    def _build_tab_provider(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)

        self._radio_google = QRadioButton("Google Translate (бесплатно, без ключа)")
        self._radio_deepl = QRadioButton("DeepL API (лучшее качество)")
        v.addWidget(self._radio_google)
        v.addWidget(self._radio_deepl)

        row = QHBoxLayout()
        row.addWidget(QLabel("Ключ DeepL API:"))
        self._deepl_key = QLineEdit()
        self._deepl_key.setEchoMode(QLineEdit.Password)
        self._deepl_key.setPlaceholderText("Вставьте ваш API-ключ DeepL")
        row.addWidget(self._deepl_key)
        v.addLayout(row)
        v.addStretch(1)
        return w

    def _build_tab_ocr(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)

        self._gpu = QCheckBox("Ускорение на GPU (требуется NVIDIA CUDA)")
        v.addWidget(self._gpu)

        note = QLabel(
            "<small><i>Ускоряет распознавание в 3-5 раз при наличии NVIDIA-видеокарты. "
            "Нужна установка torch с CUDA и перезапуск приложения.</i></small>"
        )
        note.setWordWrap(True)
        v.addWidget(note)

        form = QFormLayout()
        self._confidence = QDoubleSpinBox()
        self._confidence.setRange(0.0, 1.0)
        self._confidence.setSingleStep(0.05)
        self._confidence.setDecimals(2)
        form.addRow("Мин. уверенность OCR:", self._confidence)
        v.addLayout(form)
        v.addStretch(1)
        return w

    def _build_tab_overlay(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)

        self._opacity = QDoubleSpinBox()
        self._opacity.setRange(0.1, 1.0)
        self._opacity.setSingleStep(0.05)
        self._opacity.setDecimals(2)
        form.addRow("Непрозрачность фона:", self._opacity)

        self._font_size = QSpinBox()
        self._font_size.setRange(6, 48)
        self._font_size.setSuffix(" пт")
        form.addRow("Размер шрифта:", self._font_size)

        self._auto_font = QCheckBox("Авто-подбор размера шрифта (под DPI экрана)")
        form.addRow(self._auto_font)

        # U3: выбор темы оформления
        self._theme = QComboBox()
        self._theme.addItem("Системная (авто)", "auto")
        self._theme.addItem("Светлая",          "light")
        self._theme.addItem("Тёмная",           "dark")
        form.addRow("Тема интерфейса:", self._theme)
        return w

    # -------- state --------
    def _load_values(self) -> None:
        c = self._config
        self._hk_select.setText(c.get("general", "hotkey_select", fallback="ctrl+shift+t"))
        self._hk_toggle.setText(c.get("general", "hotkey_toggle", fallback="ctrl+shift+p"))
        self._interval.setValue(c.getint("general", "capture_interval_ms", fallback=1500))

        src = c.get("languages", "source_lang", fallback="auto")
        tgt = c.get("languages", "target_lang", fallback="ru")
        self._select_combo(self._source_lang, src)
        self._select_combo(self._target_lang, tgt)

        provider = c.get("translation", "provider", fallback="google")
        if provider == "deepl":
            self._radio_deepl.setChecked(True)
        else:
            self._radio_google.setChecked(True)
        self._deepl_key.setText(c.get("translation", "deepl_api_key", fallback=""))

        self._gpu.setChecked(c.getboolean("ocr", "gpu", fallback=False))
        self._confidence.setValue(c.getfloat("overlay", "min_confidence", fallback=0.4))

        self._opacity.setValue(c.getfloat("overlay", "opacity", fallback=0.85))
        self._font_size.setValue(c.getint("overlay", "font_size", fallback=13))
        self._auto_font.setChecked(c.getboolean("overlay", "auto_font", fallback=True))

    def _select_combo(self, combo: QComboBox, value: str) -> None:
        for i in range(combo.count()):
            if combo.itemData(i) == value:
                combo.setCurrentIndex(i)
                return

    def _save(self) -> None:
        c = self._config
        c["general"]["hotkey_select"] = self._hk_select.text().strip()
        c["general"]["hotkey_toggle"] = self._hk_toggle.text().strip()
        c["general"]["capture_interval_ms"] = str(self._interval.value())

        c["languages"]["source_lang"] = self._source_lang.currentData()
        c["languages"]["target_lang"] = self._target_lang.currentData()

        c["translation"]["provider"] = "deepl" if self._radio_deepl.isChecked() else "google"
        c["translation"]["deepl_api_key"] = self._deepl_key.text().strip()

        if not c.has_section("ocr"):
            c.add_section("ocr")
        c["ocr"]["gpu"] = "true" if self._gpu.isChecked() else "false"

        c["overlay"]["opacity"] = str(self._opacity.value())
        c["overlay"]["font_size"] = str(self._font_size.value())
        c["overlay"]["auto_font"] = "true" if self._auto_font.isChecked() else "false"
        c["overlay"]["min_confidence"] = str(self._confidence.value())

        # U3: тема
        if not c.has_section("ui"):
            c.add_section("ui")
        c["ui"]["theme"] = self._theme.currentData() or "auto"

        try:
            cfg_path = Path(__file__).parent.parent / "config.ini"
            with open(cfg_path, "w", encoding="utf-8") as f:
                c.write(f)
        except Exception as e:
            logger.warning(f"Не удалось сохранить config.ini: {e}")

        self.settings_saved.emit(c)
        self.accept()
