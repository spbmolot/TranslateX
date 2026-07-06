"""
OCR worker using EasyOCR.
Runs in a dedicated QThread to avoid blocking the Qt event loop.
EasyOCR Reader is initialized once (slow ~3-10s) before first use.
"""
import logging
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
from PyQt5.QtCore import QThread, pyqtSignal

logger = logging.getLogger(__name__)


def _bundled_model_dir() -> Optional[Path]:
    """
    Папка с моделями, вшитыми в сборку PyInstaller.
    В onedir-сборке data-файлы с dest "model" лежат в <_MEIPASS>/model.
    В dev-режиме (не frozen) вернёт None.
    """
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        return None
    d = Path(base) / "model"
    return d if d.is_dir() else None


def ensure_bundled_models() -> None:
    """
    Копирует вшитые в .exe модели EasyOCR в пользовательскую папку
    ~/.EasyOCR/model, если их там ещё нет.

    Зачем: EasyOCR по умолчанию ищет (и качает) модели в ~/.EasyOCR/model.
    Мы кладём базовые модели (детектор CRAFT + cyrillic_g2, покрывающий ru+en)
    прямо в дистрибутив, чтобы первый запуск работал офлайн — без скачивания
    ~100 МБ. Недостающие модели для других языков EasyOCR при необходимости
    докачает в эту же (записываемую) папку штатным образом.
    """
    src = _bundled_model_dir()
    if src is None:
        return  # dev-режим — ничего не копируем
    dst = Path.home() / ".EasyOCR" / "model"
    try:
        dst.mkdir(parents=True, exist_ok=True)
        for model_file in src.iterdir():
            if not model_file.is_file():
                continue
            target = dst / model_file.name
            if not target.exists():
                shutil.copy2(model_file, target)
                logger.info(f"Bundled OCR model installed: {model_file.name}")
    except Exception as e:
        logger.warning(f"Could not pre-install bundled OCR models: {e}")


def preprocess_for_ocr(image: np.ndarray, mode: str = "auto"):
    """
    PERF-002: даунсэмпл кадра перед EasyOCR. CRAFT-детектор масштабируется
    ~квадратично по площади: сжатие 1440p → 720p даёт ×2-4 на CPU.

    mode:
      "off"   — без изменений;
      "auto"  — если высота > 720px, сжать до 720px, но не сильнее чем ×0.5
                (консервативно: мелкий шрифт при более агрессивном сжатии
                перестаёт распознаваться);
      "<0..1>" — явный фиксированный коэффициент (например "0.5").

    Возвращает (processed_image, scale). bbox'ы результата OCR нужно
    умножить на 1/scale, чтобы вернуть их в координаты исходного кадра.
    """
    mode = (mode or "auto").strip().lower()
    if mode == "off":
        return image, 1.0
    h, w = image.shape[:2]
    if mode == "auto":
        target = 720
        if h <= target:
            return image, 1.0
        scale = max(target / h, 0.5)
    else:
        try:
            scale = float(mode)
        except (TypeError, ValueError):
            return image, 1.0
        if not (0.1 <= scale < 1.0):
            return image, 1.0
    import cv2
    new_w = max(1, int(round(w * scale)))
    new_h = max(1, int(round(h * scale)))
    resized = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_AREA)
    return resized, scale


@dataclass
class TextBlock:
    text: str
    bbox: List[List[int]]  # 4 corner points [[x1,y1],[x2,y1],[x2,y2],[x1,y2]]
    confidence: float
    # Медианный цвет фона вокруг bbox (r, g, b) — для inpaint-подложки
    # overlay. Проставляется core.inpaint.apply_bg_colors после OCR.
    bg_rgb: Optional[Tuple[int, int, int]] = None


class OcrWorker(QThread):
    """
    Processes screen captures with EasyOCR and emits detected text blocks.
    One instance lives for the lifetime of the app; jobs are submitted via process().
    """
    ocr_complete = pyqtSignal(object)   # list[TextBlock]
    ready = pyqtSignal()              # emitted when Reader is initialized
    error = pyqtSignal(str)

    def __init__(self, languages: List[str], min_confidence: float = 0.4,
                 use_gpu: bool = False, parent=None):
        super().__init__(parent)
        self._languages = languages
        self._min_confidence = min_confidence
        self._use_gpu = use_gpu
        self._reader = None

    def run(self) -> None:
        """Initialize EasyOCR (blocking) then enter idle loop."""
        try:
            import easyocr
            # Скопировать вшитые модели в ~/.EasyOCR/model, чтобы первый запуск
            # шёл офлайн (без скачивания ~100 МБ) для базовых языков.
            ensure_bundled_models()
            logger.info(f"Initializing EasyOCR for languages: {self._languages}")
            logger.info(f"GPU acceleration: {'enabled' if self._use_gpu else 'disabled'}")
            self._reader = easyocr.Reader(
                self._languages,
                gpu=self._use_gpu,
                verbose=False,
            )
            logger.info("EasyOCR ready")
            self.ready.emit()
        except Exception as e:
            logger.error(f"EasyOCR init failed: {e}")
            self.error.emit(str(e))
            return

        # Reader готов. Дальше реальный OCR делают отдельные OcrJobThread'ы,
        # а этот поток должен просто спать до stop(). Используем Qt event loop
        # (exec_) вместо busy-loop `while: msleep(50)` — тот будил CPU 20 раз/сек
        # впустую. quit() из stop() корректно завершает exec_().
        self.exec_()

    def stop(self) -> None:
        self.quit()
        self.wait(3000)


class OcrJobThread(QThread):
    """
    Lightweight one-shot thread for a single OCR job.
    Created fresh for each capture tick to avoid blocking OcrWorker's idle loop.
    """
    ocr_complete = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, reader, image: np.ndarray, min_confidence: float,
                 downscale: str = "auto", parent=None):
        super().__init__(parent)
        self._reader = reader
        self._image = image
        self._min_confidence = min_confidence
        self._downscale = downscale

    def run(self) -> None:
        try:
            # PERF-002: сжимаем кадр перед детектором, bbox'ы возвращаем
            # в координатах ИСХОДНОГО кадра (умножаем на 1/scale).
            image, scale = preprocess_for_ocr(self._image, self._downscale)
            inv = 1.0 / scale
            results = self._reader.readtext(image, detail=1, paragraph=False)
            blocks = []
            for bbox, text, conf in results:
                text = text.strip()
                if not text or conf < self._min_confidence:
                    continue
                # Convert EasyOCR bbox format to list of int lists
                int_bbox = [
                    [int(round(p[0] * inv)), int(round(p[1] * inv))]
                    for p in bbox
                ]
                blocks.append(TextBlock(text=text, bbox=int_bbox, confidence=float(conf)))
            self.ocr_complete.emit(blocks)
        except Exception as e:
            logger.error(f"OCR job error: {e}")
            self.ocr_complete.emit([])
