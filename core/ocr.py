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
                 use_gpu: bool = False, backend: str = "easyocr", parent=None):
        super().__init__(parent)
        self._languages = languages
        self._min_confidence = min_confidence
        self._use_gpu = use_gpu
        self._backend = backend
        self._reader = None

    def _try_init_onnx(self) -> bool:
        """PERF-006: torch-free бэкенд на onnxruntime. False → фоллбэк easyocr."""
        try:
            from core.ocr_backends.onnx_backend import OnnxReader, find_model_dir
            model_dir = find_model_dir()
            if model_dir is None:
                logger.warning(
                    "ONNX-модели не найдены (сгенерируйте scripts/export_onnx.py) "
                    "— фоллбэк на EasyOCR")
                return False
            self._reader = OnnxReader(model_dir, languages=self._languages)
            logger.info("ONNX OCR backend ready")
            return True
        except Exception as e:
            logger.warning(f"ONNX backend init failed ({e}) — фоллбэк на EasyOCR")
            return False

    def run(self) -> None:
        """Initialize OCR backend (blocking) then enter idle loop."""
        if self._backend == "onnx" and self._try_init_onnx():
            self.ready.emit()
            self.exec_()
            return
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

    PERF-007 (detector-skip): если переданы cached_boxes (детекция с прошлого
    кадра, в координатах исходного кадра) и reader умеет detect/recognize —
    детектор (самая дорогая стадия) пропускается, только распознавание.
    При полном прогоне свежие боксы кладутся в self.last_boxes для кэша.
    """
    ocr_complete = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, reader, image: np.ndarray, min_confidence: float,
                 downscale: str = "auto", cached_boxes=None, parent=None):
        super().__init__(parent)
        self._reader = reader
        self._image = image
        self._min_confidence = min_confidence
        self._downscale = downscale
        self._cached_boxes = cached_boxes
        self.used_cached_boxes = cached_boxes is not None
        self.last_boxes = None  # (horizontal_list, free_list) в координатах кадра

    @staticmethod
    def _scale_boxes(boxes, factor: float):
        """Масштабирует (horizontal_list, free_list). horizontal_list —
        [x_min, x_max, y_min, y_max]; free_list — полигоны из точек."""
        h_list = [
            [int(round(float(v) * factor)) for v in box] for box in boxes[0]
        ]
        f_list = [
            [[float(p[0]) * factor, float(p[1]) * factor] for p in poly]
            for poly in boxes[1]
        ]
        return h_list, f_list

    def run(self) -> None:
        try:
            # PERF-002: сжимаем кадр перед детектором, bbox'ы возвращаем
            # в координатах ИСХОДНОГО кадра (умножаем на 1/scale).
            image, scale = preprocess_for_ocr(self._image, self._downscale)
            inv = 1.0 / scale

            can_split = hasattr(self._reader, "detect") and hasattr(self._reader, "recognize")
            if can_split:
                if self._cached_boxes is not None:
                    # PERF-007: recognize-only по кэшированным боксам
                    h_list, f_list = self._scale_boxes(self._cached_boxes, scale)
                else:
                    h_lists, f_lists = self._reader.detect(image)
                    h_list, f_list = h_lists[0], f_lists[0]
                    # Кэш — в координатах исходного кадра
                    self.last_boxes = self._scale_boxes((h_list, f_list), inv)
                results = self._reader.recognize(
                    image, h_list, f_list, detail=1, paragraph=False)
            else:
                # Реадер без detect/recognize (например, мок) — старый путь
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
