"""
PERF-006: OCR-бэкенд на onnxruntime — БЕЗ torch и без импорта easyocr.

Использует модели, экспортированные scripts/export_onnx.py:
  craft.onnx            — детектор текста (CRAFT, fp32)
  recognizer[.int8].onnx — распознаватель (VGG+BiLSTM+CTC)
  meta.json             — алфавит, высота входа, языки

API совместим с easyocr.Reader по используемому в проекте подмножеству:
  OnnxReader.readtext(image_rgb, detail=1, paragraph=False)
    → [(bbox_4pt, text, confidence), ...]

Отличие от easyocr: вход трактуется как RGB (как отдаёт core.capture),
а не BGR; contrast-повтор для низкоконтрастных строк не реализован (v1).
"""
import json
import logging
import sys
from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import numpy as np

from core.ocr_backends import _easyocr_vendor as V

logger = logging.getLogger(__name__)

# Дефолты детекции — как у easyocr.Reader.detect/readtext
_CANVAS_SIZE = 2560
_MAG_RATIO = 1.0
_TEXT_THRESHOLD = 0.7
_LINK_THRESHOLD = 0.4
_LOW_TEXT = 0.4
_MIN_SIZE = 20
# Минимальная ширина кропа для распознавателя (VGG сжимает ширину /4)
_MIN_REC_WIDTH = 32


def find_model_dir() -> Optional[Path]:
    """Каталог с ONNX-моделями: PyInstaller-бандл или dev-checkout."""
    candidates = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "models_onnx")
    candidates.append(
        Path(__file__).resolve().parent.parent.parent / "assets" / "models" / "onnx")
    for c in candidates:
        if (c / "craft.onnx").exists() and (c / "meta.json").exists():
            return c
    return None


class OnnxReader:
    """Torch-free замена easyocr.Reader для detect+recognize."""

    def __init__(self, model_dir: Path, prefer_int8: bool = True,
                 languages: Optional[List[str]] = None):
        import onnxruntime as ort

        model_dir = Path(model_dir)
        meta = json.loads((model_dir / "meta.json").read_text(encoding="utf-8"))
        self.langs: List[str] = meta["langs"]
        self._chars: List[str] = meta["characters"]
        self._imgH: int = int(meta["model_height"])

        if languages:
            missing = set(languages) - set(self.langs) - {"en"}
            if missing:
                raise ValueError(
                    f"ONNX-модели экспортированы для {self.langs}, "
                    f"запрошены {languages} — не хватает {sorted(missing)}. "
                    "Переэкспортируйте scripts/export_onnx.py --langs …")

        so = ort.SessionOptions()
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        providers = ["CPUExecutionProvider"]
        self._det = ort.InferenceSession(
            str(model_dir / "craft.onnx"), so, providers=providers)

        rec_path = model_dir / "recognizer.int8.onnx"
        if not (prefer_int8 and rec_path.exists()):
            rec_path = model_dir / "recognizer.onnx"
        self._rec = ort.InferenceSession(str(rec_path), so, providers=providers)
        logger.info(
            f"OnnxReader готов: langs={self.langs}, imgH={self._imgH}, "
            f"recognizer={rec_path.name}")

    # ── Детекция ────────────────────────────────────────────────────

    def _detect(self, img_rgb: np.ndarray):
        img_resized, target_ratio, _ = V.resize_aspect_ratio(
            img_rgb, _CANVAS_SIZE, cv2.INTER_LINEAR, _MAG_RATIO)
        ratio = 1.0 / target_ratio
        x = V.normalizeMeanVariance(img_resized)
        x = x.transpose(2, 0, 1)[None].astype(np.float32)
        y = self._det.run(["y"], {"image": x})[0]
        score_text = y[0, :, :, 0]
        score_link = y[0, :, :, 1]
        det = V.getDetBoxes(score_text, score_link,
                            _TEXT_THRESHOLD, _LINK_THRESHOLD, _LOW_TEXT)
        boxes = V.adjustResultCoordinates(det[0], ratio, ratio)
        polys = [np.array(b).astype(np.int32).reshape(-1) for b in boxes]
        horizontal_list, free_list = V.group_text_box(
            polys, slope_ths=0.1, ycenter_ths=0.5, height_ths=0.5,
            width_ths=0.5, add_margin=0.1, sort_output=True)
        horizontal_list = [
            b for b in horizontal_list
            if max(b[1] - b[0], b[3] - b[2]) > _MIN_SIZE
        ]
        free_list = [
            b for b in free_list
            if max(V.calculate_ratio(*_free_wh(b)), 1) and _free_max_side(b) > _MIN_SIZE
        ]
        return horizontal_list, free_list

    # ── Распознавание ───────────────────────────────────────────────

    def _recognize_one(self, crop_grey: np.ndarray) -> Tuple[str, float]:
        h, w = crop_grey.shape[:2]
        if h != self._imgH:
            new_w = max(1, int(round(w * self._imgH / h)))
            crop_grey = cv2.resize(crop_grey, (new_w, self._imgH),
                                   interpolation=cv2.INTER_CUBIC)
            w = new_w
        if w < _MIN_REC_WIDTH:
            # реплицируем последний столбец (как NormalizePAD в easyocr)
            pad = np.repeat(crop_grey[:, -1:], _MIN_REC_WIDTH - w, axis=1)
            crop_grey = np.concatenate([crop_grey, pad], axis=1)

        x = crop_grey.astype(np.float32) / 255.0
        x = (x - 0.5) / 0.5
        x = x[None, None]  # (1,1,H,W)
        logits = self._rec.run(None, {"image": x})[0][0]  # (T, C)

        e = np.exp(logits - logits.max(axis=-1, keepdims=True))
        probs = e / e.sum(axis=-1, keepdims=True)
        idx = probs.argmax(axis=-1)
        maxp = probs.max(axis=-1)

        chars = []
        prev = -1
        for i in idx:
            if i != 0 and i != prev:
                chars.append(self._chars[i])
            prev = i
        text = "".join(chars)

        sel = maxp[idx != 0]
        if sel.size == 0:
            return text, 0.0
        # Как у easyocr: custom_mean = prod(x) ** (2 / sqrt(len(x)))
        conf = float(np.prod(sel) ** (2.0 / np.sqrt(sel.size)))
        return text, conf

    # ── Публичный API (подмножество easyocr.Reader) ─────────────────

    @staticmethod
    def _split_rgb_grey(image: np.ndarray):
        if image.ndim == 2:
            return cv2.cvtColor(image, cv2.COLOR_GRAY2RGB), image
        rgb = image[..., :3]
        return rgb, cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)

    def detect(self, image: np.ndarray, **_kw):
        """Как easyocr.Reader.detect: возвращает списки на батч из 1 кадра
        ([horizontal_list], [free_list])."""
        img_rgb, _ = self._split_rgb_grey(image)
        horizontal_list, free_list = self._detect(img_rgb)
        return [horizontal_list], [free_list]

    def recognize(self, image: np.ndarray, horizontal_list, free_list,
                  detail: int = 1, paragraph: bool = False,
                  reformat: bool = True, **_kw):
        """Распознавание на ГОТОВЫХ боксах (PERF-007: detector-skip)."""
        _, img_grey = self._split_rgb_grey(image)
        image_list, _max_width = V.get_image_list(
            horizontal_list, free_list, img_grey, model_height=self._imgH)

        results = []
        for box, crop in image_list:
            if crop is None or crop.size == 0:
                continue
            text, conf = self._recognize_one(crop)
            text = text.strip()
            if not text:
                continue
            bbox = [[int(p[0]), int(p[1])] for p in box]
            results.append((bbox, text, conf))
        return results

    def readtext(self, image: np.ndarray, detail: int = 1,
                 paragraph: bool = False):
        """image — RGB uint8 (как из core.capture) или grayscale 2D."""
        img_rgb, _ = self._split_rgb_grey(image)
        horizontal_list, free_list = self._detect(img_rgb)
        return self.recognize(image, horizontal_list, free_list,
                              detail=detail, paragraph=paragraph)


def _free_wh(poly) -> Tuple[float, float]:
    pts = np.array(poly, dtype=np.float32).reshape(-1, 2)
    w = float(pts[:, 0].max() - pts[:, 0].min())
    h = float(pts[:, 1].max() - pts[:, 1].min())
    return max(w, 1.0), max(h, 1.0)


def _free_max_side(poly) -> float:
    w, h = _free_wh(poly)
    return max(w, h)
