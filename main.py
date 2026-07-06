"""
TranslateX — Перевод текста с экрана в реальном времени.
Точка входа: DPI, Qt, склейка компонентов.

Логика работы (как Bandicam):
1. Пользователь нажимает Ctrl+Shift+T (или через трей)
2. Выбирает область экрана (затемнённый fullscreen + drag)
3. Появляется рамка-фрейм с кнопками "Старт / Стоп / Закрыть"
4. Рамку можно перемещать и ресайзить
5. Нажатие "Старт" — начинается захват, OCR, перевод, overlay
6. Нажатие "Стоп" — перевод останавливается, рамка остаётся
7. Закрыть рамку — всё скрывается
"""
import configparser
import logging
import os
import sys
from pathlib import Path

# Принудительная UTF-8 кодировка для stdout/stderr на Windows
# (иначе русский текст в логах падает с UnicodeEncodeError на cp1251).
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ── Подготовка окружения ДО import torch ──────────────────────────────────
# 1. KMP_DUPLICATE_LIB_OK=TRUE: разрешает одновременную загрузку libiomp5md.dll
#    из разных мест (torch/lib + системный/PyQt5). Без этой переменной
#    DllMain валится с WinError 1114 в frozen-сборках (PyInstaller).
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

# 2. В PyInstaller frozen-сборке: явно прописываем torch/lib в DLL search path
#    И принудительно загружаем зависимости c10.dll вручную через ctypes
#    в правильном порядке. Это известный workaround для конфликта DLL
#    с PyQt5/MSVC runtime (https://github.com/pyinstaller/pyinstaller/issues/7689).
def _prepare_torch_dll_paths() -> None:
    """
    Критичный фикс WinError 1114 для PyInstaller-бандла:
    - Форсируем порядок загрузки CRT/OMP/torch DLL через ctypes.WinDLL по полным путям
    - Сначала CRT из корня _internal/ (там новые версии, совместимые с torch),
      потом OpenMP и c10/torch_cpu из torch/lib.
    - Перехватываем LoadLibrary до того, как PyQt5 принесёт свои старые CRT.
    """
    if not getattr(sys, "frozen", False):
        return

    import ctypes
    import traceback

    base = Path(sys.executable).parent
    internal = base / "_internal" if (base / "_internal").is_dir() else base
    torch_lib = internal / "torch" / "lib"
    if not torch_lib.is_dir():
        print(f"[TranslateX] torch/lib not found near {internal}", file=sys.stderr)
        return

    # Добавляем ВСЕ критичные каталоги в DLL search path
    for p in (internal, torch_lib):
        try:
            os.add_dll_directory(str(p))
        except (AttributeError, OSError) as e:
            print(f"[TranslateX] add_dll_directory({p}) failed: {e}", file=sys.stderr)
    os.environ["PATH"] = (
        str(internal) + os.pathsep + str(torch_lib) + os.pathsep + os.environ.get("PATH", "")
    )

    def _preload(path: Path, critical: bool = False) -> bool:
        if not path.exists():
            return False
        try:
            ctypes.WinDLL(str(path))
            return True
        except OSError as e:
            tag = "CRITICAL" if critical else "warn"
            print(f"[TranslateX] preload {path.name} [{tag}] failed: {e}", file=sys.stderr)
            if critical:
                traceback.print_exc(file=sys.stderr)
            return False

    # 1) CRT — из корня _internal (там новые версии от torch/MSVC 14.3x)
    for name in ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll",
                 "msvcp140_1.dll", "msvcp140_2.dll", "concrt140.dll"):
        _preload(internal / name, critical=True)

    # 2) OMP / прочие helper'ы из torch/lib
    for name in ("libiomp5md.dll", "libiompstubs5md.dll", "uv.dll"):
        _preload(torch_lib / name)

    # 3) torch_global_deps (bootstrap) — перед c10
    _preload(torch_lib / "torch_global_deps.dll")

    # 4) САМ c10.dll — явно по абсолютному пути.
    #    Если он загрузится здесь — Windows запомнит его хэндл и уже не будет
    #    пытаться резолвить CRT через PyQt5-версии при последующем `import torch`.
    _preload(torch_lib / "c10.dll", critical=True)

    # 5) torch_cpu.dll — тяжёлый, но также зависит от CRT
    _preload(torch_lib / "torch_cpu.dll", critical=True)

_prepare_torch_dll_paths()

# ВАЖНО: torch/easyocr должны быть импортированы ДО PyQt5,
# иначе на Windows ломается загрузка c10.dll (конфликт MSVC/MKL/OMP DLL,
# идущих в комплекте с PyQt5 и torch).
try:
    import torch  # noqa: F401
except Exception as _e:  # pragma: no cover
    import traceback
    print(f"[TranslateX] torch preload FAILED: {_e}", file=sys.stderr)
    traceback.print_exc(file=sys.stderr)

from utils.win32_utils import set_dpi_awareness
set_dpi_awareness()

from PyQt5.QtCore import QObject, QRect, Qt, pyqtSlot, QTimer
from PyQt5.QtWidgets import QApplication

from core.config_validator import validate_and_fix, log_issues
from core.pipeline import AppController
from ui.overlay import TranslationOverlay
from ui.selector import RegionSelector
from ui.region_frame import RegionFrame
from ui.history_panel import HistoryPanel
from ui.settings_dialog import SettingsDialog
from ui.theme import apply_theme
from ui.tray import SystemTray
from core.paths import config_path, log_path, user_data_dir, ensure_user_config

# Уровень логирования — переключается переменной окружения TRANSLATEX_LOG=DEBUG
_log_level_name = os.environ.get("TRANSLATEX_LOG", "INFO").upper()
_log_level = getattr(logging, _log_level_name, logging.INFO)

# Файл лога — в пользовательской папке (%APPDATA%\TranslateX), а не рядом с .exe:
# Program Files доступен только на чтение, и апдейт не должен затирать лог.
user_data_dir()  # гарантируем существование каталога до открытия лог-файла
_LOG_FILE = log_path()
_log_handlers = [logging.StreamHandler(sys.stdout)]
try:
    _log_handlers.append(logging.FileHandler(str(_LOG_FILE), mode="w", encoding="utf-8"))
except Exception as _e:
    print(f"[TranslateX] cannot open log file: {_e}", file=sys.stderr)

logging.basicConfig(
    level=_log_level,
    format="%(asctime)s.%(msecs)03d [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    handlers=_log_handlers,
)
logger = logging.getLogger(__name__)
try:
    from core.__version__ import __version__ as _APP_VERSION
except Exception:  # pragma: no cover
    _APP_VERSION = "?"
logger.info(
    f"TranslateX v{_APP_VERSION} | logging={_log_level_name} | log={_LOG_FILE}"
)

# Пользовательский config.ini — в %APPDATA%\TranslateX. ensure_user_config()
# при первом запуске мигрирует config от прежней установки или создаёт его из
# дефолтного шаблона, вшитого в дистрибутив.
CONFIG_PATH = config_path()


def load_config() -> configparser.ConfigParser:
    ensure_user_config()
    config = configparser.ConfigParser()
    if CONFIG_PATH.exists():
        config.read(str(CONFIG_PATH), encoding="utf-8")
    defaults = {
        "general": {
            "hotkey_select": "ctrl+shift+t",
            "hotkey_toggle": "ctrl+shift+p",
            "hotkey_pause": "ctrl+shift+space",
            "hotkey_copy": "ctrl+shift+c",
            "hotkey_fullscreen": "ctrl+shift+f",
            "capture_interval_ms": "1500",
            "cache_ttl_seconds": "3600",
            "cache_max_size": "500",
        },
        "languages": {"source_lang": "auto", "target_lang": "ru"},
        "translation": {"provider": "google", "deepl_api_key": ""},
        "overlay": {
            # inpaint: bbox заливается сэмплированным цветом фона, оригинал
            # полностью скрыт (стиль Google Lens). outline — прежний стиль:
            # текст с обводкой поверх оригинала.
            "style": "inpaint",
            # Исключать overlay из захвата экрана (WDA_EXCLUDEFROMCAPTURE,
            # Win10 2004+): перевод не мерцает при каждом кадре. false —
            # прежний hide/show (или если exclusion глючит на конкретной
            # машине — проверка: scripts/check_capture_exclusion.py).
            "exclude_from_capture": "true",
            "opacity": "0.0",  # для style=outline: прозрачный фон, текст с обводкой
            "font_size": "13",
            "auto_font": "true",
            "font_color": "#ffffff",
            "bg_color": "#1a1a2e",
            "min_confidence": "0.4",
        },
        "ocr": {
            "gpu": "false",
            # Склейка OCR-строк в абзацы перед переводом (качество перевода)
            "group_paragraphs": "true",
            # PERF-002: даунсэмпл кадра перед OCR: auto | off | коэффициент 0..1
            "downscale": "auto",
        },
        "ui": {"theme": "auto"},  # auto | light | dark (U3)
    }
    for section, values in defaults.items():
        if not config.has_section(section):
            config.add_section(section)
        for key, value in values.items():
            if not config.has_option(section, key):
                config.set(section, key, value)

    # A2: валидация значений (типы/диапазоны/whitelist) после применения
    # дефолтов. Битые поля чинятся in-place — приложение всегда стартует
    # с рабочим config'ом. issues вернутся в main() для показа пользователю.
    config._validation_issues = validate_and_fix(config)  # type: ignore[attr-defined]
    log_issues(config._validation_issues)  # type: ignore[attr-defined]
    return config


class App(QObject):
    def __init__(self, qapp: QApplication, config: configparser.ConfigParser):
        super().__init__()
        self._config = config
        self._qapp = qapp

        # Компоненты
        self._controller = AppController(config)
        self._overlay = TranslationOverlay()
        self._selector = RegionSelector()
        self._frame = RegionFrame()
        self._history = HistoryPanel()
        self._tray = SystemTray()

        self._ocr_ready = False
        self._hotkeys_registered = False

        # Сигналы контроллера
        self._controller.translation_ready.connect(self._on_translation_ready)
        # История переводов: пишет в HistoryPanel при каждом успешном переводе
        self._controller.translation_ready.connect(self._history.add_entries)
        self._controller.ocr_ready.connect(self._on_ocr_ready)
        # PERF-003: OCR грузится лениво при первом Старте — спиннер по сигналу
        self._controller.ocr_loading.connect(self._frame.set_ocr_loading)
        self._controller.ocr_loading.connect(self._tray.set_ocr_loading)
        self._controller.status_message.connect(self._tray.set_status)
        # Overlay не должен попадать в кадр захвата
        self._controller.capture_about_to_start.connect(self._overlay.hide_overlay_for_capture)
        self._controller.capture_finished.connect(self._overlay.restore_after_capture)
        # Счётчики в тулбаре фрейма
        self._controller.ocr_progress.connect(self._frame.on_capture_done)
        self._controller.translation_progress.connect(self._frame.on_translation_done)

        # Состояние рамки до открытия селектора (чтобы вернуть при отмене)
        self._frame_visible_before_selector = False

        # Сигналы селектора → фрейм
        self._selector.region_selected.connect(self._on_region_selected)
        self._selector.cancelled.connect(self._on_selector_cancelled)

        # Сигналы фрейма
        self._frame.start_requested.connect(self._start_translation)
        self._frame.stop_requested.connect(self._stop_translation)
        self._frame.close_requested.connect(self._close_frame)
        self._frame.region_changed.connect(self._on_region_moved)
        self._frame.select_region_requested.connect(self._show_selector)
        # Пауза/возобновление перевода при перетаскивании или ресайзе рамки.
        self._frame.translation_pause_requested.connect(self._controller.pause)
        self._frame.translation_resume_requested.connect(self._controller.resume)
        self._frame.settings_requested.connect(self._show_settings)
        self._frame.history_requested.connect(self._show_history)
        self._frame.pause_toggle_requested.connect(self._controller.toggle_pause)
        # AppController «пингует» рамку, чтобы она показывала paused/resumed UI.
        self._controller.user_pause_changed.connect(self._frame.set_paused)

        # Сигналы трея
        self._tray.select_region_requested.connect(self._show_selector)
        self._tray.fullscreen_requested.connect(self._translate_fullscreen)
        self._tray.toggle_requested.connect(self._toggle_translation)
        self._tray.settings_requested.connect(self._show_settings)
        self._tray.quit_requested.connect(self._quit)

        # PERF-003: OCR не грузится при старте приложения — только при первом
        # запуске перевода. Трей показывает нейтральное «ожидание».
        self._tray.show()

        QTimer.singleShot(500, self._register_hotkeys)
        # Показываем рамку захвата сразу при запуске (Bandicam-style)
        QTimer.singleShot(100, self._show_default_frame)

    # ── Горячие клавиши ──────────────────────────────────────────────

    def _register_hotkeys(self) -> None:
        try:
            import keyboard
            hk_select = self._config.get("general", "hotkey_select", fallback="ctrl+shift+t")
            hk_toggle = self._config.get("general", "hotkey_toggle", fallback="ctrl+shift+p")
            hk_pause = self._config.get("general", "hotkey_pause", fallback="ctrl+shift+space")
            hk_copy = self._config.get("general", "hotkey_copy", fallback="ctrl+shift+c")
            hk_fullscreen = self._config.get(
                "general", "hotkey_fullscreen", fallback="ctrl+shift+f")

            keyboard.add_hotkey(hk_select, lambda: QTimer.singleShot(0, self._show_selector), suppress=False)
            keyboard.add_hotkey(hk_toggle, lambda: QTimer.singleShot(0, self._toggle_translation), suppress=False)
            keyboard.add_hotkey(hk_pause, lambda: QTimer.singleShot(0, self._controller.toggle_pause), suppress=False)
            keyboard.add_hotkey(hk_copy, lambda: QTimer.singleShot(0, self._copy_last_translation), suppress=False)
            keyboard.add_hotkey(hk_fullscreen, lambda: QTimer.singleShot(0, self._translate_fullscreen), suppress=False)
            self._hotkeys_registered = True
            logger.info(
                f"Горячие клавиши: {hk_select}, {hk_toggle}, {hk_pause} (pause), "
                f"{hk_copy} (copy), {hk_fullscreen} (fullscreen)"
            )
        except Exception as e:
            logger.warning(f"Не удалось зарегистрировать горячие клавиши: {e}")

    # ── OCR готовность ───────────────────────────────────────────────

    @pyqtSlot()
    def _on_ocr_ready(self) -> None:
        self._ocr_ready = True
        self._tray.set_status("готов")
        # Скрываем индикатор «загрузка OCR…» в тулбаре рамки.
        self._frame.set_ocr_ready()
        logger.info("OCR-движок инициализирован")

    # ── Выбор области ────────────────────────────────────────────────

    @pyqtSlot()
    def _show_selector(self) -> None:
        # Останавливаем перевод, но прячем рамку временно (чтобы вернуть при отмене)
        self._frame_visible_before_selector = self._frame.isVisible()
        if self._frame_visible_before_selector:
            self._stop_translation()
            self._frame.set_translating(False)
            self._frame.hide()
        self._selector.activate()

    @pyqtSlot()
    def _on_selector_cancelled(self) -> None:
        logger.info("Выбор области отменён")
        # Возвращаем рамку, если она была показана до этого
        if self._frame_visible_before_selector:
            self._frame.show()
            self._frame.raise_()

    def _show_default_frame(self) -> None:
        """Показываем рамку по центру ТОГО монитора, где сейчас курсор —
        чтобы на multi-monitor рамка всегда появлялась под рукой пользователя,
        а не на primary-экране в чужой части стола."""
        from PyQt5.QtGui import QCursor
        cursor_pos = QCursor.pos()
        screen = QApplication.screenAt(cursor_pos) or QApplication.primaryScreen()
        avail = screen.availableGeometry()
        w, h = 640, 220
        rect = QRect(
            avail.x() + (avail.width() - w) // 2,
            avail.y() + (avail.height() - h) // 2,
            w, h,
        )
        # show_at сам эмитит region_changed → _on_region_moved → set_region
        self._frame.show_at(rect)
        logger.info(
            f"Рамка захвата показана: {rect.width()}×{rect.height()} "
            f"на экране '{screen.name()}'"
        )

    @pyqtSlot(QRect)
    def _on_region_selected(self, region: QRect) -> None:
        """Область выбрана → показываем фрейм Bandicam-style."""
        # show_at сам эмитит region_changed → set_region
        self._frame.show_at(region)
        logger.info(f"Область закреплена: {region.width()}×{region.height()}")

    @pyqtSlot(QRect)
    def _on_region_moved(self, region: QRect) -> None:
        """Фрейм перемещён/ресайзнут — обновляем область захвата."""
        self._controller.set_region(region)

    # ── Старт / Стоп / Закрыть ───────────────────────────────────────

    @pyqtSlot()
    def _start_translation(self) -> None:
        # PERF-003: не блокируем запуск, пока OCR не готов — pipeline сам
        # лениво поднимет worker, покажет «Загрузка OCR…» и начнёт переводить,
        # как только Reader догрузится (клик второй раз не нужен).
        region = self._frame.capture_rect()
        self._controller.set_region(region)
        self._controller.start()
        self._tray.set_active(True)

    @pyqtSlot()
    def _stop_translation(self) -> None:
        self._controller.stop()
        self._overlay.hide_overlay()
        self._tray.set_active(False)

    @pyqtSlot()
    def _close_frame(self) -> None:
        self._stop_translation()
        self._frame.hide()

    @pyqtSlot()
    def _toggle_translation(self) -> None:
        if self._controller._active:
            self._stop_translation()
            self._frame.set_translating(False)
        else:
            if self._frame.isVisible():
                self._start_translation()
                self._frame.set_translating(True)
            else:
                self._tray.set_status("Сначала выберите область (Ctrl+Shift+T)")

    # ── Результат перевода ───────────────────────────────────────────

    @pyqtSlot(object, QRect)
    def _on_translation_ready(self, blocks: list, region: QRect) -> None:
        self._overlay.apply_config(
            opacity=self._config.getfloat("overlay", "opacity", fallback=0.0),
            font_size=self._config.getint("overlay", "font_size", fallback=13),
            font_color=self._config.get("overlay", "font_color", fallback="#ffffff"),
            bg_color=self._config.get("overlay", "bg_color", fallback="#1a1a2e"),
            auto_font=self._config.getboolean("overlay", "auto_font", fallback=True),
            style=self._config.get("overlay", "style", fallback="inpaint"),
            exclude_from_capture=self._config.getboolean(
                "overlay", "exclude_from_capture", fallback=True),
        )
        self._overlay.update_blocks(blocks, region)

    # ── Полноэкранный перевод (как «перевести страницу» у Яндекса) ────

    @pyqtSlot()
    def _translate_fullscreen(self) -> None:
        """Переводит весь монитор под курсором одним действием — рамка не
        нужна. Остановка: Ctrl+Shift+P, двойной клик по трею или меню."""
        from PyQt5.QtGui import QCursor
        self._stop_translation()
        self._frame.set_translating(False)
        self._frame.hide()
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        geo = screen.geometry()
        self._controller.set_region(QRect(geo))
        self._controller.start()
        self._tray.set_active(True)
        logger.info(
            f"Полноэкранный перевод: {geo.width()}×{geo.height()} "
            f"на экране '{screen.name()}'"
        )

    # ── Копирование перевода (Ctrl+Shift+C) ──────────────────────────

    @pyqtSlot()
    def _copy_last_translation(self) -> None:
        """Кладёт текст последнего перевода в буфер обмена. Overlay
        click-through и с него нельзя выделить текст — хоткей решает это."""
        text = self._controller.last_translation_text()
        if not text:
            self._tray.set_status("нет перевода для копирования")
            return
        QApplication.clipboard().setText(text)
        self._tray.set_status("перевод скопирован")
        if self._frame.isVisible():
            self._frame.show_toast("Скопировано")
        logger.info(f"Перевод скопирован в буфер ({len(text)} символов)")

    # ── Настройки ────────────────────────────────────────────────────

    @pyqtSlot()
    def _show_settings(self) -> None:
        dialog = SettingsDialog(self._config)
        dialog.settings_saved.connect(self._on_settings_saved)
        dialog.exec_()

    @pyqtSlot()
    def _show_history(self) -> None:
        """Открывает (или поднимает) non-modal окно истории переводов."""
        self._history.show()
        self._history.raise_()
        self._history.activateWindow()

    @pyqtSlot(object)
    def _on_settings_saved(self, config: configparser.ConfigParser) -> None:
        self._config = config
        self._controller.reload_config(config)
        # U3: реаплай темы (вдруг пользователь переключил light↔dark в Настройках).
        apply_theme(self._qapp, config.get("ui", "theme", fallback="auto"))
        # Перекрасить кастомную рамку (тулбар/бордюр рисуются вручную, не через QSS).
        self._frame.refresh_theme()
        if self._hotkeys_registered:
            try:
                import keyboard
                keyboard.unhook_all_hotkeys()
            except Exception:
                pass
            self._register_hotkeys()

    # ── Выход ────────────────────────────────────────────────────────

    @pyqtSlot()
    def _quit(self) -> None:
        logger.info("Завершение TranslateX")
        self._close_frame()
        self._overlay.hide()
        self._history.hide()
        self._controller.shutdown()
        self._qapp.quit()


def _install_excepthook() -> None:
    """
    Ставит глобальный sys.excepthook и threading.excepthook, чтобы ЛЮБОЕ
    необработанное исключение (в т.ч. из Qt-слота) попадало в translatex.log
    с полным traceback. Без этого PyQt5 просто роняет Qt5Core с 0xc0000409
    и мы не видим Python-причину.
    """
    import threading as _threading
    _orig_excepthook = sys.excepthook

    def _hook(exc_type, exc_value, exc_tb):
        try:
            logger.critical(
                "UNCAUGHT EXCEPTION",
                exc_info=(exc_type, exc_value, exc_tb),
            )
        except Exception:
            pass
        _orig_excepthook(exc_type, exc_value, exc_tb)

    def _thread_hook(args):
        logger.critical(
            f"UNCAUGHT EXCEPTION in thread {args.thread.name}",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = _hook
    _threading.excepthook = _thread_hook


def main() -> None:
    _install_excepthook()

    # ВАЖНО: Qt high-DPI scaling ВЫКЛЮЧЕН.
    # Причина: mss (захват экрана) работает в ФИЗИЧЕСКИХ пикселях, а если Qt
    # выдаёт координаты в логических — frame.x()/y() и mss-захват рассинхронятся
    # на HiDPI-мониторах (DPI 125/150%). В итоге mss захватывал область выше
    # capture_rect и OCR читал тулбар самой рамки. Per-Monitor-V2 awareness
    # уже включён через set_dpi_awareness() — этого достаточно.
    QApplication.setAttribute(Qt.AA_DisableHighDpiScaling)

    qapp = QApplication(sys.argv)
    qapp.setApplicationName("TranslateX")
    qapp.setQuitOnLastWindowClosed(False)

    config = load_config()

    # U3: применение темы оформления (auto/light/dark) ДО создания виджетов.
    apply_theme(qapp, config.get("ui", "theme", fallback="auto"))

    # A2: если при валидации config'а нашлись проблемы — показываем
    # неблокирующий QMessageBox.warning со списком (и тем, что починили).
    issues = getattr(config, "_validation_issues", []) or []
    if issues:
        from PyQt5.QtWidgets import QMessageBox
        details = "\n".join(f"• {m}" for m in issues)
        QMessageBox.warning(
            None,
            "TranslateX — config.ini",
            "Некоторые значения в config.ini были неверны и заменены на "
            "значения по умолчанию:\n\n" + details +
            "\n\nИсправленный конфиг сохранится при следующем 'Сохранить' в Настройках.",
        )

    app = App(qapp, config)

    sys.exit(qapp.exec_())


if __name__ == "__main__":
    main()
