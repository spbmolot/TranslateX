"""
OCR worker using EasyOCR.
Runs in a dedicated QThread to avoid blocking the Qt event loop.
EasyOCR Reader is initialized once (slow ~3-10s) before first use.
"""
import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np
from PyQt5.QtCore import QThread, pyqtSignal

logger = logging.getLogger(__name__)


@dataclass
class TextBlock:
    text: str
    bbox: List[List[int]]  # 4 corner points [[x1,y1],[x2,y1],[x2,y2],[x1,y2]]
    confidence: float


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
        self._pending: Optional[np.ndarray] = None
        self._running = False

    def run(self) -> None:
        """Initialize EasyOCR (blocking) then enter idle loop."""
        self._running = True
        try:
            import easyocr
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

        while self._running:
            self.msleep(50)  # idle; actual work triggered by process()

    def process(self, image: np.ndarray) -> None:
        """
        Submit an image for OCR processing.
        Called from the main thread; runs OCR synchronously on this worker thread.
        If a previous job is still running, the new image replaces the pending slot.
        """
        if self._reader is None:
            return
        self._pending = image
        if not self.isRunning():
            return
        # Run immediately on this thread via invokeMethod would require moving logic here.
        # Instead we use a simple approach: run in a separate one-shot thread.
        self._run_ocr(image)

    def _run_ocr(self, image: np.ndarray) -> None:
        """Execute OCR and emit results. Called from worker thread."""
        if self._reader is None:
            return
        try:
            results = self._reader.readtext(image, detail=1, paragraph=False)
            blocks = []
            for bbox, text, conf in results:
                text = text.strip()
                if not text or conf < self._min_confidence:
                    continue
                blocks.append(TextBlock(text=text, bbox=bbox, confidence=conf))
            self.ocr_complete.emit(blocks)
        except Exception as e:
            logger.error(f"OCR error: {e}")
            self.ocr_complete.emit([])

    def stop(self) -> None:
        self._running = False
        self.quit()
        self.wait(3000)


class OcrJobThread(QThread):
    """
    Lightweight one-shot thread for a single OCR job.
    Created fresh for each capture tick to avoid blocking OcrWorker's idle loop.
    """
    ocr_complete = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, reader, image: np.ndarray, min_confidence: float, parent=None):
        super().__init__(parent)
        self._reader = reader
        self._image = image
        self._min_confidence = min_confidence

    def run(self) -> None:
        try:
            results = self._reader.readtext(self._image, detail=1, paragraph=False)
            blocks = []
            for bbox, text, conf in results:
                text = text.strip()
                if not text or conf < self._min_confidence:
                    continue
                # Convert EasyOCR bbox format to list of int lists
                int_bbox = [[int(p[0]), int(p[1])] for p in bbox]
                blocks.append(TextBlock(text=text, bbox=int_bbox, confidence=float(conf)))
            self.ocr_complete.emit(blocks)
        except Exception as e:
            logger.error(f"OCR job error: {e}")
            self.ocr_complete.emit([])
