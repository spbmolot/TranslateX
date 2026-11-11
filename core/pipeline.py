"""
AppController — central orchestrator.
Wires capture → OCR → translation → overlay.
Owns the capture timer and manages worker thread lifecycle.
"""
import configparser
import logging
import time
from typing import Optional

import numpy as np
from PyQt5.QtCore import QObject, QRect, Qt, QTimer, pyqtSignal, pyqtSlot
from PyQt5.QtWidgets import QApplication

from core import local_llm, local_mt, models
from core.cache import TranslationCache, normalize_text
from core.capture import capture_to_numpy
from core.inpaint import apply_bg_colors
from core.ocr import OcrWorker, OcrJobThread, TextBlock
from core.paragraphs import group_paragraphs
from core.translator import TranslationJobThread, TranslatedBlock, is_already_in_lang

logger = logging.getLogger(__name__)


class AppController(QObject):
    # Emitted when translated blocks are ready for the overlay
    translation_ready = pyqtSignal(object, QRect)  # list[TranslatedBlock], capture_region
    # Emitted when OCR engine is initialized and ready
    ocr_ready = pyqtSignal()
    # Emitted when lazy OCR worker starts loading (PERF-003) — UI показывает спиннер
    ocr_loading = pyqtSignal()
    # Emitted on errors for status display
    status_message = pyqtSignal(str)
    # Emitted right before/after a screen grab — overlay should hide/show accordingly
    capture_about_to_start = pyqtSignal()
    capture_finished = pyqtSignal()
    # Статистика для UI
    ocr_progress = pyqtSignal(int)        # число найденных OCR-блоков
    translation_progress = pyqtSignal(int)  # число переведённых блоков
    user_pause_changed = pyqtSignal(bool)   # True=paused, False=resumed (для UI кнопки)
    # Сервис перевода перестал отвечать (строка — причина) / снова отвечает.
    # Эмитятся один раз на серию сбоев, а не на каждый неудачный запрос.
    translation_failed = pyqtSignal(str)
    translation_recovered = pyqtSignal()

    # Backoff повторов перевода после сбоя: 2, 4, 8 … не больше 60 секунд
    _RETRY_MAX_DELAY_S = 60
    # Медленные провайдеры: одновременно только один джоб перевода, новый
    # текст прерывает текущий (иначе на CPU копилась бы очередь на минуты).
    _SERIAL_PROVIDERS = {"local_llm"}
    # Автосохранение кэша переводов на диск (если были изменения)
    _CACHE_AUTOSAVE_MS = 60_000

    def __init__(self, config: configparser.ConfigParser, parent=None):
        super().__init__(parent)
        self._config = config
        self._region: Optional[QRect] = None
        self._active = False
        # Явная пауза от пользователя (hotkey / кнопка). Отличается от
        # временной auto-паузы при drag/resize рамки: drag-resume не должен
        # выводить из user-pause, иначе явная пауза самопроизвольно сбрасывалась бы.
        self._user_paused = False
        self._ocr_busy = False
        self._current_ocr_job: Optional[OcrJobThread] = None
        self._current_trans_job: Optional[TranslationJobThread] = None
        # PERF-010: capture следующего кадра идёт параллельно с переводом
        # предыдущего, поэтому translation-джобы могут кратко перекрываться.
        # Монотонный id отбрасывает результат устаревшего джоба, если более
        # новый уже успел завершиться (иначе старый перевод перезаписал бы
        # свежий на overlay).
        self._trans_job_seq = 0
        self._last_applied_trans_id = 0
        # Счётчики для логов (меняются в одном потоке)
        self._tick_count = 0
        self._capture_count = 0
        self._ocr_count = 0
        self._trans_count = 0
        self._last_ocr_start_ts = 0.0
        self._last_trans_start_ts = 0.0
        self._last_capture_ts = 0.0
        self._warned_not_ready = False  # чтобы не спамить в логи
        # Дедуп: сигнатура (текст блоков) последнего OCR — если не изменилась,
        # перевод не запускаем заново. Кэш для overlay сохраняется в _last_translated.
        self._last_ocr_signature: Optional[tuple] = None
        self._last_translated_blocks: Optional[list] = None
        # Frame-diff: даунсэмпленая сигнатура последнего кадра. Если новый
        # кадр почти не изменился — пропускаем OCR (на статичном экране
        # это экономит 80-95% CPU EasyOCR).
        self._last_frame_signature: Optional[np.ndarray] = None
        self._last_frame_shape: Optional[tuple] = None
        self._frame_skip_count = 0  # сколько кадров подряд скипнули по diff
        # Последний захваченный кадр — для сэмплирования цвета фона (inpaint)
        self._last_capture_image: Optional[np.ndarray] = None
        # PERF-007 (detector-skip): боксы последней полной детекции
        # (в координатах кадра) + счётчик recognize-only прогонов подряд.
        # Полный detect — каждый 3-й OCR-прогон (кэп устаревания боксов).
        self._last_det_boxes = None
        self._det_skip_count = 0

        # Cache
        self._cache = TranslationCache(
            max_size=config.getint("general", "cache_max_size", fallback=500),
            ttl_seconds=config.getint("general", "cache_ttl_seconds", fallback=3600),
        )

        # PERF-003: OCR worker создаётся ЛЕНИВО при первом start().
        # EasyOCR + модели — это секунды загрузки и сотни МБ RAM; пока
        # пользователь не запустил перевод, они не нужны (быстрый cold start,
        # лёгкий idle в трее). Примечание: torch всё равно импортируется в
        # main.py до PyQt5 (DLL-порядок), так что экономия — Reader и модели.
        self._ocr_worker: Optional[OcrWorker] = None

        # Capture timer (single-shot: event-driven reschedule после завершения
        # OCR/translation вместо постоянного тика — экономит CPU и убирает
        # лог-спам 'OCR still busy, skipping frame').
        interval = config.getint("general", "capture_interval_ms", fallback=1500)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        # PERF-012: точный таймер — джиттер тика ±2мс вместо ±20мс у CoarseTimer
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.setInterval(interval)
        self._timer.timeout.connect(self._on_timer_tick)

        # Сбой перевода: повторяем ТОЛЬКО перевод тех же блоков (без нового
        # OCR) с нарастающей задержкой. Раньше при неизменном экране повтора
        # не было вовсе: дедуп по тексту переиспользовал «перевод»-оригинал.
        self._trans_fail_streak = 0
        self._retry_blocks: Optional[list] = None
        # Учёт джобов перевода «в полёте» (см. _SERIAL_PROVIDERS)
        self._trans_running = 0
        self._inflight_signature: Optional[tuple] = None
        self._queued_blocks: Optional[list] = None
        models.configure(config)
        self._retry_timer = QTimer(self)
        self._retry_timer.setSingleShot(True)
        self._retry_timer.timeout.connect(self._retry_translation)

        # Кэш раньше писался только при штатном выходе и терялся при сбое.
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(self._CACHE_AUTOSAVE_MS)
        self._autosave_timer.timeout.connect(self._cache.save_if_dirty)
        self._autosave_timer.start()

    def _ocr_languages(self):
        source = self._config.get("languages", "source_lang", fallback="en")
        target = self._config.get("languages", "target_lang", fallback="ru")
        langs = set()
        if source != "auto":
            langs.add(source[:2])
        else:
            # «Авто» — НЕ автоопределение: EasyOCR требует список языков до
            # инициализации Reader'а. Фактически распознаются en + target;
            # CJK/арабский при auto не распознаются вовсе (это отражено в UI).
            logger.info(
                "source_lang=auto: OCR ограничен языками en+%s. "
                "Для CJK/арабского нужно выбрать исходный язык явно.",
                target[:2],
            )
        langs.add(target[:2])
        # Always include English as fallback
        langs.add("en")
        # EasyOCR uses 2-letter codes for most languages
        # Map common languages
        lang_map = {"zh": "ch_sim", "ja": "ja", "ko": "ko"}
        result = []
        for l in langs:
            result.append(lang_map.get(l, l))
        return result

    @pyqtSlot()
    def _on_ocr_ready(self):
        logger.info("OCR engine ready")
        self._warned_not_ready = False
        self.ocr_ready.emit()
        self.status_message.emit("OCR готов")

    @pyqtSlot(str)
    def _on_ocr_error(self, msg: str):
        logger.error(f"OCR error: {msg}")
        self.status_message.emit(f"Ошибка OCR: {msg}")

    @pyqtSlot(QRect)
    def set_region(self, region: QRect) -> None:
        """Called when user selects a screen area."""
        prev = self._region
        self._region = region
        # Если регион действительно сместился — сбрасываем дедуп-кэш,
        # чтобы новая область гарантированно перерисовалась.
        if prev is None or prev != region:
            self._last_ocr_signature = None
            self._last_translated_blocks = None
            # Сигнатура кадра тоже становится невалидной (другой размер
            # и другое содержимое — нельзя сравнивать).
            self._last_frame_signature = None
            self._last_frame_shape = None
            self._frame_skip_count = 0
            # Боксы детекции принадлежали старой области
            self._last_det_boxes = None
            self._det_skip_count = 0
        # DEBUG, не INFO: рамку пользователь может двигать десятки раз
        # в секунду — это засоряло бы production-лог сотнями записей.
        logger.debug(f"Capture region set: {region}")

    def _ensure_ocr_worker(self) -> None:
        """PERF-003: создать и запустить OCR worker при первом обращении."""
        if self._ocr_worker is not None:
            return
        langs = self._ocr_languages()
        self._ocr_worker = OcrWorker(
            languages=langs,
            min_confidence=self._config.getfloat("overlay", "min_confidence", fallback=0.4),
            use_gpu=self._config.getboolean("ocr", "gpu", fallback=False),
            backend=self._config.get("ocr", "backend", fallback="easyocr"),
            parent=self,
        )
        self._ocr_worker.ready.connect(self._on_ocr_ready)
        self._ocr_worker.error.connect(self._on_ocr_error)
        self._ocr_worker.start()
        self.ocr_loading.emit()
        logger.info(f"OCR worker started lazily (langs={langs})")

    @pyqtSlot()
    def start(self) -> None:
        if self._region is None:
            self.status_message.emit("Сначала выберите область")
            return
        # Лениво поднимаем OCR: пока Reader грузится, тики будут
        # пропускаться со статусом «Загрузка OCR…», затем перевод пойдёт сам.
        self._ensure_ocr_worker()
        self._active = True
        self._tick_count = 0
        self._capture_count = 0
        self._ocr_count = 0
        self._trans_count = 0
        self._timer.start()
        self.status_message.emit("Перевод…")
        reader_ok = self._ocr_worker._reader is not None
        logger.info(
            f"Pipeline started. region={self._region.width()}x{self._region.height()}, "
            f"interval={self._timer.interval()}ms, OCR_ready={reader_ok}, "
            f"langs={self._ocr_languages()}, target={self._config.get('languages', 'target_lang', fallback='ru')}"
        )

    @pyqtSlot()
    def stop(self) -> None:
        self._active = False
        self._user_paused = False  # сброс, чтобы при следующем start() не были на паузе
        self._timer.stop()
        self._retry_timer.stop()
        self._retry_blocks = None
        self._trans_fail_streak = 0
        self._queued_blocks = None
        self.status_message.emit("Остановлено")
        logger.info(
            f"Pipeline stopped. ticks={self._tick_count}, captures={self._capture_count}, "
            f"ocr_jobs={self._ocr_count}, translations={self._trans_count}"
        )

    @pyqtSlot()
    def toggle(self) -> None:
        if self._active:
            self.stop()
        else:
            self.start()

    @pyqtSlot()
    def pause(self) -> None:
        """Временная пауза захвата (например, при перетаскивании рамки).
        Сохраняет _active=True, но останавливает таймер. Resume — через resume()."""
        if self._active and self._timer.isActive():
            self._timer.stop()
            logger.debug("Pipeline paused (timer stopped)")

    @pyqtSlot()
    def resume(self) -> None:
        """Возобновляет захват после auto-паузы (drag/resize). Если
        пользователь поставил явную паузу — игнорируем, она снимается
        только через toggle_pause()/user_resume()."""
        if self._user_paused:
            logger.debug("resume() ignored: user paused explicitly")
            return
        if self._active and not self._timer.isActive():
            self._timer.start()
            logger.debug("Pipeline resumed (timer restarted)")

    @pyqtSlot()
    def toggle_pause(self) -> None:
        """Явная пауза/возобновление пользователем (hotkey или кнопка).
        Если перевод не активен — no-op."""
        if not self._active:
            return
        if self._user_paused:
            self._user_paused = False
            if not self._timer.isActive():
                self._timer.start()
            self.status_message.emit("Перевод…")
            self.user_pause_changed.emit(False)
            logger.info("Pipeline RESUMED by user")
        else:
            self._user_paused = True
            self._timer.stop()
            self.status_message.emit("Пауза")
            self.user_pause_changed.emit(True)
            logger.info("Pipeline PAUSED by user")

    def reload_config(self, config: configparser.ConfigParser) -> None:
        """Apply updated settings without restarting."""
        old_langs = set(self._ocr_languages())
        old_gpu = self._config.getboolean("ocr", "gpu", fallback=False)
        old_backend = self._config.get("ocr", "backend", fallback="easyocr")
        old_provider = self._provider()
        self._config = config
        models.configure(config)
        new_provider = self._provider()
        if new_provider != old_provider:
            # Освобождаем память прежнего локального движка и заставляем
            # текущий экран перевестись заново новым провайдером.
            if old_provider == "local_llm":
                local_llm.server().stop()
            elif old_provider == "local_fast":
                local_mt.unload()
            self._last_ocr_signature = None
            self._last_frame_signature = None
            self._queued_blocks = None
        interval = config.getint("general", "capture_interval_ms", fallback=1500)
        self._timer.setInterval(interval)
        self._cache.configure(
            max_size=config.getint("general", "cache_max_size", fallback=500),
            ttl_seconds=config.getint("general", "cache_ttl_seconds", fallback=3600),
        )
        # Провайдер/ключ могли измениться — прежняя серия сбоев неактуальна,
        # отложенный повтор выполняем сразу с новыми настройками.
        self._trans_fail_streak = 0
        if self._retry_timer.isActive():
            self._retry_timer.start(0)

        # Смена языков или GPU требует пересоздания EasyOCR Reader'а —
        # раньше настройка молча не применялась до перезапуска приложения.
        # Lazy-load делает рестарт дешёвым: убиваем воркер, следующий
        # start() поднимет его с новыми параметрами (и продолжит перевод,
        # если он был активен).
        new_langs = set(self._ocr_languages())
        new_gpu = config.getboolean("ocr", "gpu", fallback=False)
        new_backend = config.get("ocr", "backend", fallback="easyocr")
        if self._ocr_worker is not None and (
            new_langs != old_langs or new_gpu != old_gpu or new_backend != old_backend
        ):
            was_active = self._active
            if was_active:
                self.stop()
            self._ocr_worker.stop()
            self._ocr_worker = None
            # Дедуп-кэши больше невалидны: другой Reader может распознать иначе
            self._last_ocr_signature = None
            self._last_translated_blocks = None
            logger.info(
                f"OCR worker перезапускается: языки {sorted(old_langs)} → "
                f"{sorted(new_langs)}, gpu {old_gpu} → {new_gpu}, "
                f"backend {old_backend} → {new_backend}"
            )
            if was_active:
                self.start()  # лениво пересоздаст воркер с новым конфигом

    # ── Frame-diff helpers ─────────────────────────────────────────────

    @staticmethod
    def _frame_signature(image: np.ndarray) -> np.ndarray:
        """
        Сигнатура кадра: даунсемпл до ≤32×32 grayscale через cv2.INTER_AREA.
        Именно area-усреднение (а не stride-сэмплинг, как раньше): при stride
        строка текста ниже шага сэмплирования (напр. 22px при шаге 33 на 1080p)
        могла целиком попасть МЕЖДУ выборками и изменение не замечалось вовсе.
        INTER_AREA учитывает каждый пиксель; стоит ~1-2мс на 1080p — раз в тик
        (1.5с) это пренебрежимо. Используется только для сравнения кадров.
        """
        import cv2
        if image.ndim == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
        else:
            gray = image
        h, w = gray.shape[:2]
        out_w = min(32, w)
        out_h = min(32, h)
        sig = cv2.resize(gray, (out_w, out_h), interpolation=cv2.INTER_AREA)
        return sig.astype(np.float32)

    def _frame_unchanged(self, image: np.ndarray, threshold: float = 4.0) -> bool:
        """
        True если новый кадр практически идентичен предыдущему.

        Сравниваем НЕ глобальное среднее (оно размывает локальные изменения:
        одно изменившееся слово в большом регионе давало mean < 2 и кадр
        считался «неизменным» до 30 скипов подряд ≈ 45 с устаревшего перевода),
        а максимум по тайлам ~4×4 сигнатуры — worst-case локальный diff.
        threshold=4.0 (на uint8 [0..255]) — выше шума субпикселей/антиалиасинга
        (тайловый mean шума ±1 ≈ 0.5), но любое реальное изменение текста
        даёт в своём тайле diff ≥ 15-20.
        """
        sig = self._frame_signature(image)
        prev = self._last_frame_signature
        prev_shape = getattr(self, "_last_frame_shape", None)
        self._last_frame_signature = sig
        self._last_frame_shape = image.shape
        # Сигнатура теперь всегда ≤32×32, поэтому смену размера региона
        # ловим по shape исходного кадра, а не по shape сигнатуры.
        if prev is None or prev.shape != sig.shape or prev_shape != image.shape:
            return False
        diff = np.abs(sig - prev)
        h, w = diff.shape
        th, tw = max(1, h // 8), max(1, w // 8)
        hh, ww = (h // th) * th, (w // tw) * tw
        tiles = diff[:hh, :ww].reshape(hh // th, th, ww // tw, tw).mean(axis=(1, 3))
        # Хвостовые строки/столбцы, не кратные тайлу, учитываем отдельно,
        # чтобы изменение у правого/нижнего края не потерялось.
        tile_max = float(tiles.max())
        if ww < w:
            tile_max = max(tile_max, float(diff[:, ww:].mean()))
        if hh < h:
            tile_max = max(tile_max, float(diff[hh:, :].mean()))
        return tile_max < threshold

    def _next_cached_boxes(self):
        """PERF-007: боксы для recognize-only прогона или None (полный detect).
        Политика: не больше 2 recognize-only подряд, затем полный detect —
        ограничивает устаревание раскладки ~3 тиками."""
        if (
            self._config.getboolean("ocr", "detector_skip", fallback=True)
            and self._last_det_boxes is not None
            and self._det_skip_count < 2
        ):
            self._det_skip_count += 1
            return self._last_det_boxes
        self._det_skip_count = 0
        return None

    def _reschedule_tick(self) -> None:
        """Планирует следующий capture-тик. Таймер single-shot: перезапускается
        ПОСЛЕ завершения текущего цикла (OCR/translation), а не тикает без остановки.
        Это убирает 'OCR still busy' лог-спам и освобождает CPU когда цикл идёт."""
        if self._active and not self._timer.isActive():
            self._timer.start()

    @pyqtSlot()
    def _on_timer_tick(self) -> None:
        self._tick_count += 1
        if not self._active or self._region is None:
            return
        # _ocr_busy guard не нужен: таймер single-shot останавливается при
        # запуске OCR job'а и перезапускается в _on_ocr_complete / _on_translation_complete.
        if self._ocr_worker is None or self._ocr_worker._reader is None:
            # Предупреждаем один раз вместо спама
            if not self._warned_not_ready:
                logger.warning(
                    f"Tick #{self._tick_count}: OCR reader not ready yet, "
                    "waiting for EasyOCR to finish loading models..."
                )
                self.status_message.emit("Загрузка OCR… подождите")
                self._warned_not_ready = True
            # Повторим попытку через interval (когда reader доготовится).
            self._reschedule_tick()
            return

        r = self._region
        logger.debug(
            f"Tick #{self._tick_count}: capturing region "
            f"x={r.x()} y={r.y()} w={r.width()} h={r.height()}"
        )

        # Скрываем overlay перед захватом, чтобы он не попал в кадр
        # (иначе OCR прочитает уже переведённый текст → бесконечный цикл)
        self.capture_about_to_start.emit()
        QApplication.processEvents()

        cap_ts = time.time()
        try:
            image = capture_to_numpy(r.x(), r.y(), r.width(), r.height())
        except Exception as e:
            logger.error(f"Capture failed at tick #{self._tick_count}: {e}")
            self.status_message.emit("Ошибка захвата экрана — повтор")
            # Без перепланирования single-shot таймер больше не сработал бы
            # и перевод молча встал бы до ручного Стоп/Старт.
            self._reschedule_tick()
            return
        finally:
            self.capture_finished.emit()

        self._capture_count += 1
        cap_ms = (time.time() - cap_ts) * 1000
        self._last_capture_ts = time.time()
        logger.debug(
            f"Capture #{self._capture_count} OK: shape={image.shape}, "
            f"dtype={image.dtype}, took {cap_ms:.0f}ms"
        )

        # Frame-diff guard: если кадр практически идентичен предыдущему —
        # OCR не запускаем (экономия CPU на статичных экранах). Через
        # каждые ~30 скипнутых кадров (≈ 15-45с) всё равно делаем OCR
        # для синхронизации (вдруг блоки исчезли/появились без визуального
        # изменения, или модель что-то распознает иначе).
        if self._frame_unchanged(image) and self._frame_skip_count < 30:
            self._frame_skip_count += 1
            logger.debug(
                f"Frame unchanged (skip #{self._frame_skip_count}), reusing last overlay"
            )
            # Переотображаем последний overlay (на случай если он был скрыт
            # перед захватом hide_overlay_for_capture'ом)
            if (
                self._last_translated_blocks is not None
                and self._region is not None
                # пока идёт перевод нового текста, старый не возвращаем
                and self._trans_running == 0
            ):
                self.translation_ready.emit(self._last_translated_blocks, self._region)
            self._reschedule_tick()
            return
        self._frame_skip_count = 0

        self._ocr_busy = True
        self._last_ocr_start_ts = time.time()
        # Кадр нужен в _on_ocr_complete для сэмплирования цвета фона
        # под inpaint-подложку overlay.
        self._last_capture_image = image
        job = OcrJobThread(
            reader=self._ocr_worker._reader,
            image=image,
            min_confidence=self._config.getfloat("overlay", "min_confidence", fallback=0.4),
            downscale=self._config.get("ocr", "downscale", fallback="auto"),
            cached_boxes=self._next_cached_boxes(),
            parent=self,
        )
        job.ocr_complete.connect(self._on_ocr_complete)
        job.finished.connect(job.deleteLater)
        self._current_ocr_job = job
        job.start()
        logger.debug(f"OCR job #{self._capture_count} started")

    @pyqtSlot(object)
    def _on_ocr_complete(self, blocks: list) -> None:
        self._ocr_busy = False
        self._ocr_count += 1
        # PERF-007: обновляем кэш боксов детекции. Полный прогон приносит
        # свежие боксы; слабый recognize-only результат (пусто или низкая
        # средняя уверенность — текст, вероятно, сместился) инвалидирует
        # кэш, чтобы следующий тик сделал полный detect.
        job = self.sender()
        if job is not None:
            fresh_boxes = getattr(job, "last_boxes", None)
            if fresh_boxes is not None:
                self._last_det_boxes = fresh_boxes
            elif getattr(job, "used_cached_boxes", False):
                weak = (
                    not blocks
                    or (sum(b.confidence for b in blocks) / len(blocks)) < 0.3
                )
                if weak:
                    logger.debug(
                        "Detector-skip: слабый recognize-only результат — "
                        "следующий тик сделает полный detect"
                    )
                    self._last_det_boxes = None
        elapsed_ms = (time.time() - self._last_ocr_start_ts) * 1000
        n_blocks = len(blocks) if blocks else 0
        logger.info(
            f"OCR #{self._ocr_count}: {n_blocks} block(s) in {elapsed_ms:.0f}ms"
        )
        if n_blocks > 0 and logger.isEnabledFor(logging.DEBUG):
            for i, b in enumerate(blocks[:5]):
                text_preview = (b.text[:40] + "…") if len(b.text) > 40 else b.text
                logger.debug(f"  block[{i}] conf={b.confidence:.2f} text={text_preview!r}")

        # Сигнал UI для счётчика
        self.ocr_progress.emit(n_blocks)

        if not blocks or self._region is None or not self._active:
            if not blocks:
                logger.debug(
                    "OCR вернул 0 блоков: в области нет текста или confidence низкий"
                )
            self._reschedule_tick()
            return

        # Группировка построчных OCR-блоков в абзацы: переводчик получает
        # связные предложения вместо разорванных строк (ключевое качество
        # перевода — «как Яндекс», а не построчная каша).
        if self._config.getboolean("ocr", "group_paragraphs", fallback=True):
            grouped = group_paragraphs(blocks)
            if len(grouped) != len(blocks):
                logger.debug(
                    f"Paragraph grouping: {len(blocks)} line(s) → "
                    f"{len(grouped)} block(s)"
                )
            blocks = grouped

        # Цвет фона под каждым блоком (для inpaint-подложки overlay).
        apply_bg_colors(getattr(self, "_last_capture_image", None), blocks)

        # Фильтрация: блоки, которые уже на target-языке — не переводим вообще
        # (например, русский → русский). На overlay их тоже не показываем,
        # чтобы не накладывать «перевод» поверх оригинального русского текста.
        target_lang = self._config.get("languages", "target_lang", fallback="ru")
        filtered = [b for b in blocks if not is_already_in_lang(b.text or "", target_lang)]
        skipped = len(blocks) - len(filtered)
        if skipped:
            logger.debug(
                f"Skipped {skipped}/{len(blocks)} block(s) already in target lang "
                f"'{target_lang}' (no translation needed)"
            )
        if not filtered:
            # Всё было на целевом языке — overlay скрываем, дедуп-кэш обновим как пустой
            self._last_ocr_signature = tuple()
            self._last_translated_blocks = []
            self.translation_ready.emit([], self._region)
            self._reschedule_tick()
            return
        blocks = filtered

        # Дедуп: если набор распознанных строк не изменился с прошлого раза —
        # просто переотображаем кэшированный перевод, не дёргая API/кэш заново.
        # Сигнатура нормализована (lower + схлоп пробелов, как ключ кэша):
        # OCR-дрожание регистра/пробелов между кадрами не считается изменением.
        signature = tuple(normalize_text(b.text or "") for b in blocks)
        if self._trans_running > 0 and signature == self._inflight_signature:
            # Этот же текст уже переводится — ждём результат, не дублируем
            # запрос и не возвращаем на экран перевод прежнего текста.
            self._reschedule_tick()
            return
        if (
            signature == self._last_ocr_signature
            and self._last_translated_blocks is not None
        ):
            logger.debug(
                f"OCR identical to previous frame ({len(blocks)} blocks) — "
                "skipping translation, reusing cached overlay"
            )
            # Текст тот же, но координаты могли сдвинуться (скролл) —
            # пересобираем блоки с НОВЫМИ bbox/цветами и старыми переводами.
            # Раньше эмитились блоки со старыми координатами и перевод
            # «отставал» от прокрученного контента.
            if len(self._last_translated_blocks) == len(blocks):
                self._last_translated_blocks = [
                    TranslatedBlock(
                        original=b.text,
                        translated=old.translated,
                        bbox=b.bbox,
                        confidence=b.confidence,
                        bg_rgb=getattr(b, "bg_rgb", None),
                    )
                    for b, old in zip(blocks, self._last_translated_blocks)
                ]
            if self._region is not None:
                self.translation_ready.emit(self._last_translated_blocks, self._region)
            self._reschedule_tick()
            return
        self._last_ocr_signature = signature

        if self._provider() in self._SERIAL_PROVIDERS and self._trans_running > 0:
            # Медленный провайдер ещё переводит прежний текст: прерываем
            # его (готовые блоки уже в кэше) и ставим свежий текст в очередь.
            self._queued_blocks = blocks
            try:
                if self._current_trans_job is not None:
                    self._current_trans_job.requestInterruption()
            except RuntimeError:
                pass  # объект уже удалён Qt-ом
            self._reschedule_tick()
            return

        # Идёт backoff после сбоя сервиса: в сеть не ходим (cache_only),
        # а отложенный повтор переключаем на свежие блоки.
        # Признак — серия сбоев, а не активность таймера: пока повторный
        # джоб в полёте, таймер уже неактивен, и обычный цикл слал бы второй
        # сетевой запрос параллельно с повтором.
        in_backoff = self._trans_fail_streak > 0
        if in_backoff:
            self._retry_blocks = blocks
        self._start_translation_job(blocks, cache_only=in_backoff)
        # PERF-010: не ждём конца перевода — планируем следующий capture
        # сразу. OCR следующего кадра пойдёт параллельно с HTTP-переводом
        # текущего (обе стадии в своих потоках), throughput выше.
        self._reschedule_tick()

    def _provider(self) -> str:
        return self._config.get("translation", "provider", fallback="google")

    def _start_translation_job(self, blocks: list, cache_only: bool = False) -> None:
        self._last_trans_start_ts = time.time()
        self._trans_job_seq += 1
        self._trans_running += 1
        self._inflight_signature = self._signature(blocks)
        if (self._provider() == "local_llm" and not cache_only
                and not local_llm.server().is_running()):
            self.status_message.emit("Загрузка модели перевода…")
        job = TranslationJobThread(
            blocks=blocks,
            target_lang=self._config.get("languages", "target_lang", fallback="ru"),
            source_lang=self._config.get("languages", "source_lang", fallback="auto"),
            provider=self._config.get("translation", "provider", fallback="google"),
            deepl_api_key=self._config.get("translation", "deepl_api_key", fallback=""),
            cache=self._cache,
            parent=self,
            cache_only=cache_only,
            llm_device=self._config.get("local", "llm_device", fallback="auto"),
        )
        job._job_id = self._trans_job_seq
        job.translation_complete.connect(self._on_translation_complete)
        job.translation_partial.connect(self._on_translation_partial)
        job.finished.connect(job.deleteLater)
        self._current_trans_job = job
        job.start()
        logger.debug(
            f"Translation job #{self._trans_job_seq} started for {len(blocks)} "
            f"block(s){' [cache only]' if cache_only else ''}"
        )

    # ── Сбой сервиса перевода: уведомление + повтор с backoff ──────────

    def _track_translation_health(self, job) -> None:
        """Разбирает итог джоба: полный сбой → backoff-повтор и уведомление,
        успех после серии сбоев → «восстановлено»."""
        attempted = int(getattr(job, "attempted_count", 0) or 0)
        failed = int(getattr(job, "failed_count", 0) or 0)
        if not attempted:
            return  # только кэш — о состоянии сервиса ничего не узнали
        if failed < attempted:
            if self._trans_fail_streak:
                logger.info("Сервис перевода снова отвечает")
                self._trans_fail_streak = 0
                self._retry_timer.stop()
                pending = self._retry_blocks
                self._retry_blocks = None
                self.status_message.emit("Перевод…")
                self.translation_recovered.emit()
                # Пока шёл повтор, на экране появился другой текст (он был
                # показан cache-only) — переводим его сразу, иначе дедуп
                # оставил бы оригинал до следующего изменения экрана.
                if (
                    pending
                    and pending is not getattr(job, "source_blocks", None)
                    and self._active
                    and self._signature(pending) == self._last_ocr_signature
                ):
                    self._start_translation_job(pending)
            return
        # Ни один блок не перевёлся — сервис недоступен/ключ неверный/бан.
        self._trans_fail_streak += 1
        delay = min(self._RETRY_MAX_DELAY_S, 2 ** self._trans_fail_streak)
        reason = getattr(job, "error_text", "") or "сервис перевода недоступен"
        # Если за время запроса пришёл более свежий текст — повторяем его.
        if self._retry_blocks is None:
            self._retry_blocks = getattr(job, "source_blocks", None)
        self._retry_timer.start(delay * 1000)
        logger.warning(
            f"Перевод не удался ({reason}), попытка #{self._trans_fail_streak}; "
            f"повтор через {delay} с"
        )
        self.status_message.emit(f"Ошибка перевода: {reason}. Повтор через {delay} с")
        if self._trans_fail_streak == 1:
            self.translation_failed.emit(reason)

    @pyqtSlot()
    def _retry_translation(self) -> None:
        blocks = self._retry_blocks
        if not self._active or not blocks:
            self._trans_fail_streak = 0
            self._retry_blocks = None
            return
        if self._user_paused:
            # На паузе в сеть не ходим; проверим снова позже.
            self._retry_timer.start(self._RETRY_MAX_DELAY_S * 1000)
            return
        if self._signature(blocks) != self._last_ocr_signature:
            # Текст исчез/сменилась область — повторять нечего. Снимаем
            # backoff, следующий текст переведёт обычный цикл.
            self._trans_fail_streak = 0
            self._retry_blocks = None
            return
        self._start_translation_job(blocks)

    @staticmethod
    def _signature(blocks: list) -> tuple:
        return tuple(normalize_text(b.text or "") for b in blocks)

    def _start_queued(self) -> None:
        """Запустить перевод текста, пришедшего во время работы медленного
        провайдера (если он всё ещё на экране)."""
        blocks, self._queued_blocks = self._queued_blocks, None
        if (
            blocks
            and self._active
            and self._trans_running == 0
            and self._signature(blocks) == self._last_ocr_signature
        ):
            self._start_translation_job(
                blocks, cache_only=self._trans_fail_streak > 0)

    @pyqtSlot(object)
    def _on_translation_partial(self, translated_blocks: list) -> None:
        """Промежуточный результат медленного провайдера: показываем уже
        переведённые блоки, не дожидаясь остальных."""
        sender = self.sender()
        job_id = getattr(sender, "_job_id", None) if sender is not None else None
        if job_id is not None and job_id != self._trans_job_seq:
            return  # уже запущен более новый джоб
        if self._queued_blocks is not None or not self._active:
            return  # текст на экране сменился — этот результат устарел
        if self._region is not None:
            self.translation_ready.emit(translated_blocks, self._region)

    @pyqtSlot(object)
    def _on_translation_complete(self, translated_blocks: list) -> None:
        self._trans_running = max(0, self._trans_running - 1)
        if self._trans_running == 0:
            self._inflight_signature = None
        _sender = self.sender()
        if _sender is not None and getattr(_sender, "interrupted", False) is True:
            # Прерван ради более свежего текста: неполный результат на
            # overlay не применяем (готовые блоки уже лежат в кэше).
            logger.debug("Translation job interrupted — starting queued text")
            self._start_queued()
            self._reschedule_tick()
            return
        # PERF-010: отбрасываем результат джоба, если результат более НОВОГО
        # джоба уже применён к overlay (перевод старого всё равно лёг в кэш).
        # Просто «запущен более новый» — не повод дропать: старый результат
        # валиден, пока новый не пришёл. sender() is None при прямом вызове
        # в тестах — считаем актуальным.
        sender = self.sender()
        job_id = getattr(sender, "_job_id", None) if sender is not None else None
        if job_id is not None:
            if job_id <= self._last_applied_trans_id:
                logger.debug(
                    f"Translation job #{job_id} dropped "
                    f"(overlay already has #{self._last_applied_trans_id})"
                )
                self._start_queued()
                self._reschedule_tick()
                return
            self._last_applied_trans_id = job_id
        if sender is not None:
            self._track_translation_health(sender)
        self._trans_count += 1
        elapsed_ms = (time.time() - self._last_trans_start_ts) * 1000
        n = len(translated_blocks) if translated_blocks else 0
        logger.info(
            f"Translation #{self._trans_count}: {n} block(s) in {elapsed_ms:.0f}ms"
        )
        if n > 0 and logger.isEnabledFor(logging.DEBUG):
            for i, b in enumerate(translated_blocks[:3]):
                try:
                    src = b.original or ""
                    dst = b.translated or ""
                    src_prev = (src[:30] + "…") if len(src) > 30 else src
                    dst_prev = (dst[:30] + "…") if len(dst) > 30 else dst
                    logger.debug(f"  translated[{i}] {src_prev!r} → {dst_prev!r}")
                except Exception as _e:
                    logger.debug(f"  translated[{i}] log-format failed: {_e}")

        self.translation_progress.emit(n)
        # Кэшируем для дедуп-переотображения при неизменном тексте
        self._last_translated_blocks = translated_blocks
        if self._region is not None:
            self.translation_ready.emit(translated_blocks, self._region)
        self._start_queued()
        # Цикл capture→OCR→translate закрыт — планируем следующий capture.
        self._reschedule_tick()

    def last_translation_text(self) -> str:
        """Текст последнего перевода (для копирования в буфер обмена):
        блоки в порядке сверху-вниз, по одному на строку. Пусто, если
        перевода ещё не было."""
        blocks = self._last_translated_blocks or []
        lines = []
        for b in blocks:
            t = (b.translated or "").strip()
            if t:
                lines.append(t)
        return "\n".join(lines)

    def shutdown(self) -> None:
        """Clean shutdown — save cache, stop workers, wait for in-flight jobs."""
        self._timer.stop()
        self._retry_timer.stop()
        self._autosave_timer.stop()
        self._active = False
        for job in (self._current_ocr_job, self._current_trans_job):
            try:
                if job is not None and job.isRunning():
                    job.wait(2000)
            except RuntimeError:
                pass  # объект уже удалён Qt-ом
        self._cache.save()
        local_llm.server().stop()
        if self._ocr_worker is not None:
            self._ocr_worker.stop()
        logger.info("Pipeline shut down")
