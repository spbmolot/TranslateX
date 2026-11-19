"""
Диалог настроек — компактный, разбит на вкладки (QTabWidget).
Вкладки: Общее · Языки · Перевод · OCR · Отображение.
"""
import configparser
import copy
import io
import logging
from pathlib import Path

from PyQt5.QtCore import QIODevice, QSaveFile, QSize, pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QFrame, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QRadioButton, QScrollArea, QSpinBox, QTabWidget,
    QVBoxLayout, QWidget,
)

from core import hardware, local_mt, models
from core.__version__ import __version__
from core.shortcuts import HOTKEY_DEFAULTS, parse_hotkey
from core.paths import config_path, user_data_dir

logger = logging.getLogger(__name__)

LANGUAGES = [
    # «auto» намеренно НЕ обещает «авто-определение»: EasyOCR требует список
    # языков заранее, при auto распознаются только латиница + язык перевода.
    ("auto", "Авто (латиница + язык перевода)"),
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
        self._config = copy.deepcopy(config)
        self._gpu_info = hardware.detect_gpu()
        self.setWindowTitle(f"TranslateX v{__version__} — Настройки")
        self._build_ui()
        self._load_values()
        # On a scaled laptop screen the tabs can scroll while Save stays visible.
        available = self.screen().availableGeometry().size() - QSize(32, 64)
        self.resize(self.sizeHint().expandedTo(QSize(520, 460)).boundedTo(available))

    # -------- UI --------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        self._tabs = QTabWidget()
        for page, title in (
            (self._build_tab_general(), "Общее"),
            (self._build_tab_languages(), "Языки"),
            (self._build_tab_provider(), "Перевод"),
            (self._build_tab_ocr(), "OCR"),
            (self._build_tab_overlay(), "Отображение"),
        ):
            scroll = QScrollArea()
            scroll.setFrameShape(QFrame.NoFrame)
            scroll.setWidgetResizable(True)
            scroll.setWidget(page)
            self._tabs.addTab(scroll, title)
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
        self._hk_select.setPlaceholderText("Отключено")
        form.addRow("Горячая клавиша — выбор области:", self._hk_select)

        self._hk_toggle = QLineEdit()
        self._hk_toggle.setPlaceholderText("Отключено")
        form.addRow("Горячая клавиша — вкл/выкл перевод:", self._hk_toggle)

        self._hk_pause = QLineEdit()
        self._hk_pause.setPlaceholderText("Отключено")
        form.addRow("Горячая клавиша — пауза:", self._hk_pause)

        self._hk_copy = QLineEdit()
        self._hk_copy.setPlaceholderText("Отключено")
        form.addRow("Горячая клавиша — скопировать перевод:", self._hk_copy)

        self._hk_fullscreen = QLineEdit()
        self._hk_fullscreen.setPlaceholderText("Отключено")
        form.addRow("Горячая клавиша — перевести весь экран:", self._hk_fullscreen)

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

        note = QLabel(
            "<small><i>Режим «Авто» распознаёт только английский и язык "
            "перевода: OCR-движку нужен список языков заранее. Для японского, "
            "китайского, корейского или арабского выберите исходный язык явно "
            "— иначе текст не будет распознан вовсе.</i></small>"
        )
        note.setWordWrap(True)
        form.addRow(note)
        return w

    def _build_tab_provider(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)

        self._radio_google = QRadioButton("Google Translate (интернет, бесплатно, без ключа)")
        self._radio_deepl = QRadioButton("DeepL API (интернет, нужен ключ)")
        self._radio_local_fast = QRadioButton(
            "Локально: быстрая модель (без интернета, процессор, мгновенно)")
        self._radio_local_llm = QRadioButton(
            "Локально: нейросеть TranslateGemma (без интернета, лучшее качество)")
        for radio in (self._radio_google, self._radio_deepl,
                      self._radio_local_fast, self._radio_local_llm):
            v.addWidget(radio)
            radio.toggled.connect(self._refresh_local_status)

        row = QHBoxLayout()
        row.addWidget(QLabel("Ключ DeepL API:"))
        self._deepl_key = QLineEdit()
        self._deepl_key.setEchoMode(QLineEdit.Password)
        self._deepl_key.setPlaceholderText("Вставьте ваш API-ключ DeepL")
        row.addWidget(self._deepl_key)
        v.addLayout(row)

        box = QGroupBox("Локальные модели")
        form = QFormLayout(box)

        self._llm_device = QComboBox()
        self._llm_device.addItem("Авто (видеокарта, если подходит)", "auto")
        self._llm_device.addItem("Видеокарта", "gpu")
        self._llm_device.addItem("Процессор", "cpu")
        self._llm_device.currentIndexChanged.connect(self._refresh_local_status)
        form.addRow("Нейросеть считать на:", self._llm_device)

        gpu_label = QLabel(f"<small>Обнаружено: {hardware.describe(self._gpu_info)}</small>")
        gpu_label.setWordWrap(True)
        form.addRow("", gpu_label)

        dir_row = QHBoxLayout()
        self._models_dir = QLineEdit()
        self._models_dir.setPlaceholderText(str(models.default_models_dir()))
        self._models_dir.editingFinished.connect(self._refresh_local_status)
        dir_row.addWidget(self._models_dir)
        browse = QPushButton("Обзор…")
        browse.clicked.connect(self._browse_models_dir)
        dir_row.addWidget(browse)
        form.addRow("Папка моделей:", dir_row)

        self._local_status = QLabel()
        self._local_status.setWordWrap(True)
        form.addRow(self._local_status)

        self._download_btn = QPushButton("Скачать")
        self._download_btn.clicked.connect(self._download_local)
        form.addRow(self._download_btn)
        v.addWidget(box)

        hint = (
            "нейросеть TranslateGemma — видеокарта подходит"
            if self._gpu_info is not None and self._gpu_info.usable
            else "быстрая модель — нейросеть без видеокарты переводит экран до минуты"
        )
        note = QLabel(f"<small><i>Для этого компьютера из локальных лучше: {hint}.</i></small>")
        note.setWordWrap(True)
        v.addWidget(note)
        v.addStretch(1)
        return w

    # -------- локальные модели --------
    def _selected_provider(self) -> str:
        if self._radio_deepl.isChecked():
            return "deepl"
        if self._radio_local_fast.isChecked():
            return "local_fast"
        if self._radio_local_llm.isChecked():
            return "local_llm"
        return "google"

    def _required_components(self) -> list:
        """Компоненты, нужные выбранному локальному провайдеру (все, включая
        уже установленные). Для сетевых провайдеров — пусто."""
        path = self._models_dir.text().strip()
        root = Path(path) if path else models.default_models_dir()
        provider = self._selected_provider()
        if provider == "local_fast":
            src = self._source_lang.currentData() or "auto"
            tgt = self._target_lang.currentData() or "ru"
            if src == "auto":
                src = "en" if tgt != "en" else "ru"
            if local_mt.route(src, tgt, root) is not None:
                return []  # прямая модель или обе половины через английский
            return [models.opus_model(src, tgt, root)]
        if provider == "local_llm":
            device = self._llm_device.currentData() or "auto"
            variants = hardware.llm_variants(device, self._gpu_info)
            comps = [models.llm_model(root), models.llama_runtime(variants[0], root)]
            if variants[0] != "cpu":
                # запасной вариант на случай, если GPU-сборка не запустится
                comps.append(models.llama_runtime("cpu", root))
            return comps
        return []

    def _refresh_local_status(self, *_args) -> None:
        if not hasattr(self, "_local_status") or not hasattr(self, "_target_lang"):
            return
        provider = self._selected_provider()
        self._llm_device.setEnabled(provider == "local_llm")
        if provider not in ("local_fast", "local_llm"):
            self._local_status.setText(
                "<small>Выберите локальный вариант, чтобы переводить без "
                "интернета — текст с экрана не будет покидать компьютер.</small>")
            self._download_btn.setVisible(False)
            return
        missing = [c for c in self._required_components() if not c.is_installed()]
        if not missing:
            self._local_status.setText("✔ Всё установлено, можно пользоваться.")
            self._download_btn.setVisible(False)
            return
        size = sum(c.size for c in missing)
        names = ", ".join(c.title for c in missing)
        self._local_status.setText(f"Нужно скачать: {names}.")
        self._download_btn.setText(f"Скачать ({models.format_size(size)})")
        self._download_btn.setVisible(True)

    def _browse_models_dir(self) -> None:
        start = self._models_dir.text().strip() or str(models.default_models_dir())
        chosen = QFileDialog.getExistingDirectory(self, "Папка для моделей", start)
        if chosen:
            self._models_dir.setText(chosen.replace("/", "\\"))
            self._refresh_local_status()

    def _download_local(self) -> bool:
        from ui.download_dialog import download_components
        ok = download_components(self, self._required_components())
        self._refresh_local_status()
        return ok

    def _build_tab_ocr(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)

        self._backend = QComboBox()
        self._backend.addItem("EasyOCR (torch, по умолчанию)", "easyocr")
        self._backend.addItem("ONNX (быстрее на CPU, эксперимент)", "onnx")
        backend_row = QFormLayout()
        backend_row.addRow("Движок распознавания:", self._backend)
        v.addLayout(backend_row)

        self._gpu = QCheckBox("Ускорение на GPU (требуется NVIDIA CUDA)")
        v.addWidget(self._gpu)

        note = QLabel(
            "<small><i>Ускоряет распознавание в 3-5 раз при наличии NVIDIA-видеокарты. "
            "Нужна установка torch с CUDA и перезапуск приложения.</i></small>"
        )
        note.setWordWrap(True)
        v.addWidget(note)

        self._group_paragraphs = QCheckBox(
            "Склеивать строки в абзацы перед переводом (лучше качество)"
        )
        v.addWidget(self._group_paragraphs)
        gp_note = QLabel(
            "<small><i>Соседние строки одного текста переводятся как целые "
            "предложения, а не построчно. Отключите, если переводите "
            "таблицы/списки, где каждая строка самостоятельна.</i></small>"
        )
        gp_note.setWordWrap(True)
        v.addWidget(gp_note)

        form = QFormLayout()
        self._confidence = QDoubleSpinBox()
        self._confidence.setRange(0.0, 1.0)
        self._confidence.setSingleStep(0.05)
        self._confidence.setDecimals(2)
        form.addRow("Мин. уверенность OCR:", self._confidence)

        # PERF-002: даунсэмпл кадра перед распознаванием
        self._downscale = QComboBox()
        self._downscale.addItem("Авто (сжимать высокие кадры)", "auto")
        self._downscale.addItem("Выключен (макс. качество)", "off")
        self._downscale.addItem("×0.75", "0.75")
        self._downscale.addItem("×0.5 (макс. скорость)", "0.5")
        form.addRow("Сжатие кадра перед OCR:", self._downscale)
        v.addLayout(form)
        v.addStretch(1)
        return w

    def _build_tab_overlay(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)

        self._style = QComboBox()
        self._style.addItem("Закрашивать оригинал (как Google Lens)", "inpaint")
        self._style.addItem("Текст с обводкой поверх оригинала", "outline")
        form.addRow("Стиль перевода:", self._style)

        self._opacity = QDoubleSpinBox()
        # Нижняя граница 0.0 — полностью прозрачный фон (дефолт приложения:
        # текст рисуется обводкой без подложки). Раньше min=0.1 клампил 0.0→0.1
        # и при сохранении необратимо включал фоновую подложку.
        self._opacity.setRange(0.0, 1.0)
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
        self._hk_select.setText(c.get("general", "hotkey_select", fallback=HOTKEY_DEFAULTS["hotkey_select"]))
        self._hk_toggle.setText(c.get("general", "hotkey_toggle", fallback=HOTKEY_DEFAULTS["hotkey_toggle"]))
        self._hk_pause.setText(
            c.get("general", "hotkey_pause", fallback=HOTKEY_DEFAULTS["hotkey_pause"]))
        self._hk_copy.setText(c.get("general", "hotkey_copy", fallback=HOTKEY_DEFAULTS["hotkey_copy"]))
        self._hk_fullscreen.setText(
            c.get("general", "hotkey_fullscreen", fallback=HOTKEY_DEFAULTS["hotkey_fullscreen"]))
        self._interval.setValue(c.getint("general", "capture_interval_ms", fallback=1500))

        src = c.get("languages", "source_lang", fallback="auto")
        tgt = c.get("languages", "target_lang", fallback="ru")
        self._select_combo(self._source_lang, src)
        self._select_combo(self._target_lang, tgt)

        provider = c.get("translation", "provider", fallback="google")
        {"deepl": self._radio_deepl, "local_fast": self._radio_local_fast,
         "local_llm": self._radio_local_llm}.get(
            provider, self._radio_google).setChecked(True)
        self._select_combo(
            self._llm_device, c.get("local", "llm_device", fallback="auto"))
        self._models_dir.setText(c.get("local", "models_dir", fallback=""))
        self._source_lang.currentIndexChanged.connect(self._refresh_local_status)
        self._target_lang.currentIndexChanged.connect(self._refresh_local_status)
        self._refresh_local_status()
        self._deepl_key.setText(c.get("translation", "deepl_api_key", fallback=""))

        self._select_combo(self._backend, c.get("ocr", "backend", fallback="easyocr"))
        self._gpu.setChecked(c.getboolean("ocr", "gpu", fallback=False))
        self._group_paragraphs.setChecked(
            c.getboolean("ocr", "group_paragraphs", fallback=True))
        self._confidence.setValue(c.getfloat("overlay", "min_confidence", fallback=0.4))
        self._select_combo(self._downscale, c.get("ocr", "downscale", fallback="auto"))

        self._select_combo(self._style, c.get("overlay", "style", fallback="inpaint"))
        self._opacity.setValue(c.getfloat("overlay", "opacity", fallback=0.0))
        self._font_size.setValue(c.getint("overlay", "font_size", fallback=13))
        self._auto_font.setChecked(c.getboolean("overlay", "auto_font", fallback=True))

        # U3: тема интерфейса. Без этой загрузки комбобокс всегда показывал
        # индекс 0 ("авто") и при «Сохранить» молча перезаписывал выбор
        # пользователя (light/dark) обратно на auto.
        self._select_combo(self._theme, c.get("ui", "theme", fallback="auto"))

    def _select_combo(self, combo: QComboBox, value: str) -> None:
        for i in range(combo.count()):
            if combo.itemData(i) == value:
                combo.setCurrentIndex(i)
                return

    def _save(self) -> None:
        used = set()
        for field in (self._hk_select, self._hk_toggle, self._hk_pause, self._hk_copy, self._hk_fullscreen):
            value = field.text().strip()
            if not value:
                continue
            try:
                combo = parse_hotkey(value)
                if combo in used:
                    raise ValueError("это сочетание назначено нескольким действиям")
                used.add(combo)
            except ValueError as exc:
                self._tabs.setCurrentIndex(0)
                field.setFocus()
                QMessageBox.warning(self, "Горячие клавиши", f"{value}: {exc}")
                return
        if self._selected_provider() == "deepl" and not self._deepl_key.text().strip():
            self._tabs.setCurrentIndex(2)
            self._deepl_key.setFocus()
            QMessageBox.warning(self, "DeepL", "Введите ключ DeepL или выберите другой переводчик.")
            return
        # Локальный провайдер без моделей: предлагаем скачать сейчас.
        missing = [x for x in self._required_components() if not x.is_installed()]
        if missing:
            size = models.format_size(sum(x.size for x in missing))
            answer = QMessageBox.question(
                self, "TranslateX",
                f"Для выбранного способа перевода нужно скачать модели ({size}).\n"
                "Скачать сейчас?",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
                QMessageBox.Yes)
            if answer == QMessageBox.Cancel:
                return
            if answer == QMessageBox.Yes and not self._download_local():
                return  # не скачалось — остаёмся в настройках

        c = self._config
        c["general"]["hotkey_select"] = self._hk_select.text().strip()
        c["general"]["hotkey_toggle"] = self._hk_toggle.text().strip()
        c["general"]["hotkey_pause"] = self._hk_pause.text().strip()
        c["general"]["hotkey_copy"] = self._hk_copy.text().strip()
        c["general"]["hotkey_fullscreen"] = self._hk_fullscreen.text().strip()
        c["general"]["capture_interval_ms"] = str(self._interval.value())

        c["languages"]["source_lang"] = self._source_lang.currentData()
        c["languages"]["target_lang"] = self._target_lang.currentData()

        c["translation"]["provider"] = self._selected_provider()
        if not c.has_section("local"):
            c.add_section("local")
        c["local"]["llm_device"] = self._llm_device.currentData() or "auto"
        c["local"]["models_dir"] = self._models_dir.text().strip().replace("%", "%%")
        c["translation"]["deepl_api_key"] = self._deepl_key.text().strip().replace("%", "%%")

        if not c.has_section("ocr"):
            c.add_section("ocr")
        c["ocr"]["backend"] = self._backend.currentData() or "easyocr"
        c["ocr"]["gpu"] = "true" if self._gpu.isChecked() else "false"
        c["ocr"]["group_paragraphs"] = (
            "true" if self._group_paragraphs.isChecked() else "false")
        c["ocr"]["downscale"] = self._downscale.currentData() or "auto"

        c["overlay"]["style"] = self._style.currentData() or "inpaint"
        c["overlay"]["opacity"] = str(self._opacity.value())
        c["overlay"]["font_size"] = str(self._font_size.value())
        c["overlay"]["auto_font"] = "true" if self._auto_font.isChecked() else "false"
        c["overlay"]["min_confidence"] = str(self._confidence.value())

        # U3: тема
        if not c.has_section("ui"):
            c.add_section("ui")
        c["ui"]["theme"] = self._theme.currentData() or "auto"

        try:
            user_data_dir()  # гарантируем существование %APPDATA%\TranslateX
            cfg_path = config_path()
            contents = io.StringIO()
            c.write(contents)
            data = contents.getvalue().encode("utf-8")
            f = QSaveFile(str(cfg_path))
            if not f.open(QIODevice.WriteOnly) or f.write(data) != len(data) or not f.commit():
                raise OSError(f.errorString())
        except (OSError, ValueError, configparser.Error) as e:
            logger.warning(f"Не удалось сохранить config.ini: {e}")
            QMessageBox.warning(self, "Настройки не сохранены", str(e))
            return

        self.settings_saved.emit(c)
        self.accept()
