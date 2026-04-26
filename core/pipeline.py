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
from PyQt5.QtCore import QObject, QRect, QTimer, pyqtSignal, pyqtSlot
from PyQt5.QtWidgets import QApplication

from core.cache import TranslationCache
from core.capture import capture_to_numpy
from core.ocr import OcrWorker, OcrJobThread, TextBlock
from core.translator import TranslationJobThread, TranslatedBlock, is_already_in_lang

logger = logging.getLogger(__name__)


class AppController(QObject):
    # Emitted when translated blocks are ready for the overlay
    translation_ready = pyqtSignal(object, QRect)  # list[TranslatedBlock], capture_region
    # Emitted when OCR engine is initialized and ready
    ocr_ready = pyqtSignal()
    # Emitted on errors for status display
    status_message = pyqtSignal(str)
    # Emitted right before/after a screen grab — overlay should hide/show accordingly
    capture_about_to_start = pyqtSignal()
    capture_finished = pyqtSignal()
    # Статистика для UI
    ocr_progress = pyqtSignal(int)        # число найденных OCR-блоков
    translation_progress = pyqtSignal(int)  # число переведённых блоков
    user_pause_changed = pyqtSignal(bool)   # True=paused, False=resumed (для UI кнопки)

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
        self._frame_skip_count = 0  # сколько кадров подряд скипнули по diff

        # Cache
        self._cache = TranslationCache(
            max_size=config.getint("general", "cache_max_size", fallback=500),
            ttl_seconds=config.getint("general", "cache_ttl_seconds", fallback=3600),
        )

        # OCR worker (persistent thread for Reader initialization)
        langs = self._ocr_languages()
        self._ocr_worker = OcrWorker(
            languages=langs,
            min_confidence=config.getfloat("overlay", "min_confidence", fallback=0.4),
            use_gpu=config.getboolean("ocr", "gpu", fallback=False),
            parent=self,
        )
        self._ocr_worker.ready.connect(self._on_ocr_ready)
        self._ocr_worker.error.connect(self._on_ocr_error)
        self._ocr_worker.start()

        # Capture timer (single-shot: event-driven reschedule после завершения
        # OCR/translation вместо постоянного тика — экономит CPU и убирает
        # лог-спам 'OCR still busy, skipping frame').
        interval = config.getint("general", "capture_interval_ms", fallback=1500)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(interval)
        self._timer.timeout.connect(self._on_timer_tick)

    def _ocr_languages(self):
        source = self._config.get("languages", "source_lang", fallback="en")
        target = self._config.get("languages", "target_lang", fallback="ru")
        langs = set()
        if source != "auto":
            langs.add(source[:2])
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
        self.status_message.emit("OCR ready")

    @pyqtSlot(str)
    def _on_ocr_error(self, msg: str):
        logger.error(f"OCR error: {msg}")
        self.status_message.emit(f"OCR error: {msg}")

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
            self._frame_skip_count = 0
        # DEBUG, не INFO: рамку пользователь может двигать десятки раз
        # в секунду — это засоряло бы production-лог сотнями записей.
        logger.debug(f"Capture region set: {region}")

    @pyqtSlot()
    def start(self) -> None:
        if self._region is None:
            self.status_message.emit("Select a region first (hotkey)")
            return
        self._active = True
        self._tick_count = 0
        self._capture_count = 0
        self._ocr_count = 0
        self._trans_count = 0
        self._timer.start()
        self.status_message.emit("Translating...")
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
        self.status_message.emit("Stopped")
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
            self.status_message.emit("Translating...")
            self.user_pause_changed.emit(False)
            logger.info("Pipeline RESUMED by user")
        else:
            self._user_paused = True
            self._timer.stop()
            self.status_message.emit("Paused")
            self.user_pause_changed.emit(True)
            logger.info("Pipeline PAUSED by user")

    def reload_config(self, config: configparser.ConfigParser) -> None:
        """Apply updated settings without restarting."""
        self._config = config
        interval = config.getint("general", "capture_interval_ms", fallback=1500)
        self._timer.setInterval(interval)
        self._cache._ttl = config.getint("general", "cache_ttl_seconds", fallback=3600)
        self._cache._max_size = config.getint("general", "cache_max_size", fallback=500)

    # ── Frame-diff helpers ─────────────────────────────────────────────

    @staticmethod
    def _frame_signature(image: np.ndarray) -> np.ndarray:
        """
        Быстрая сигнатура кадра: даунсемпл до ~32×32 grayscale через stride-индексинг.
        Стоит ~0.1мс независимо от исходного разрешения. Используется только
        для сравнения кадров между собой (не для OCR).
        """
        h, w = image.shape[:2]
        sh = max(1, h // 32)
        sw = max(1, w // 32)
        # Stride-subsample + усреднение каналов RGB → 2D float32
        sub = image[::sh, ::sw]
        if sub.ndim == 3:
            sub = sub.mean(axis=2)
        return sub.astype(np.float32)

    def _frame_unchanged(self, image: np.ndarray, threshold: float = 2.0) -> bool:
        """
        True если новый кадр практически идентичен предыдущему.
        threshold=2.0 (на uint8 [0..255]) — устойчив к шуму субпикселей,
        но чувствителен к реальному изменению текста (обычно diff > 5).
        """
        sig = self._frame_signature(image)
        prev = self._last_frame_signature
        self._last_frame_signature = sig
        if prev is None or prev.shape != sig.shape:
            return False
        diff = float(np.abs(sig - prev).mean())
        return diff < threshold

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
        if self._ocr_worker._reader is None:
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
            self.capture_finished.emit()
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
            if self._last_translated_blocks is not None and self._region is not None:
                self.translation_ready.emit(self._last_translated_blocks, self._region)
            self._reschedule_tick()
            return
        self._frame_skip_count = 0

        self._ocr_busy = True
        self._last_ocr_start_ts = time.time()
        job = OcrJobThread(
            reader=self._ocr_worker._reader,
            image=image,
            min_confidence=self._config.getfloat("overlay", "min_confidence", fallback=0.4),
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
        signature = tuple((b.text or "").strip() for b in blocks)
        if (
            signature == self._last_ocr_signature
            and self._last_translated_blocks is not None
        ):
            logger.debug(
                f"OCR identical to previous frame ({len(blocks)} blocks) — "
                "skipping translation, reusing cached overlay"
            )
            # Тихо обновим overlay (на случай если он скрыт после capture)
            if self._region is not None:
                self.translation_ready.emit(self._last_translated_blocks, self._region)
            self._reschedule_tick()
            return
        self._last_ocr_signature = signature

        self._last_trans_start_ts = time.time()
        job = TranslationJobThread(
            blocks=blocks,
            target_lang=self._config.get("languages", "target_lang", fallback="ru"),
            source_lang=self._config.get("languages", "source_lang", fallback="auto"),
            provider=self._config.get("translation", "provider", fallback="google"),
            deepl_api_key=self._config.get("translation", "deepl_api_key", fallback=""),
            cache=self._cache,
            parent=self,
        )
        job.translation_complete.connect(self._on_translation_complete)
        job.finished.connect(job.deleteLater)
        self._current_trans_job = job
        job.start()
        logger.debug(f"Translation job started for {n_blocks} block(s)")

    @pyqtSlot(object)
    def _on_translation_complete(self, translated_blocks: list) -> None:
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
        # Цикл capture→OCR→translate закрыт — планируем следующий capture.
        self._reschedule_tick()

    def shutdown(self) -> None:
        """Clean shutdown — save cache, stop workers, wait for in-flight jobs."""
        self._timer.stop()
        self._active = False
        for job in (self._current_ocr_job, self._current_trans_job):
            try:
                if job is not None and job.isRunning():
                    job.wait(2000)
            except RuntimeError:
                pass  # объект уже удалён Qt-ом
        self._cache.save()
        self._ocr_worker.stop()
        logger.info("Pipeline shut down")
