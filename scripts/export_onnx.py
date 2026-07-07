"""
PERF-006: экспорт моделей EasyOCR в ONNX для torch-free инференса.

Экспортирует (dev-only, требует torch+easyocr):
  assets/models/onnx/craft.onnx            — детектор CRAFT (fp32; conv-сеть,
                                             dynamic-квантизация ей ничего не даёт)
  assets/models/onnx/recognizer.onnx       — распознаватель cyrillic_g2 (fp32)
  assets/models/onnx/recognizer.int8.onnx  — он же, dynamic INT8 (LSTM/MatMul)
  assets/models/onnx/meta.json             — алфавит, высота входа, языки

Запуск: python scripts/export_onnx.py [--langs ru,en]
Артефакты НЕ коммитятся (см. .gitignore) — генерируются на машине сборки.
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OUT_DIR = Path(__file__).resolve().parent.parent / "assets" / "models" / "onnx"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--langs", default="ru,en")
    args = ap.parse_args()
    langs = [l.strip() for l in args.langs.split(",") if l.strip()]

    import torch
    import easyocr

    print(f"Загрузка EasyOCR Reader {langs} (quantize=False для экспорта)…")
    reader = easyocr.Reader(langs, gpu=False, verbose=False, quantize=False)

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # ── Детектор CRAFT ──────────────────────────────────────────────
    det = reader.detector
    if isinstance(det, torch.nn.DataParallel):
        det = det.module
    det.eval()
    dummy = torch.zeros(1, 3, 480, 640, dtype=torch.float32)
    craft_path = OUT_DIR / "craft.onnx"
    # dynamo=False: legacy TorchScript-экспортёр. Новый dynamo-путь падает
    # на BiLSTM распознавателя (PendingUnbackedSymbolNotFound), legacy
    # стабильно экспортирует обе модели.
    torch.onnx.export(
        det, dummy, str(craft_path),
        input_names=["image"], output_names=["y", "feature"],
        dynamic_axes={
            "image": {0: "batch", 2: "height", 3: "width"},
            "y": {0: "batch", 1: "h2", 2: "w2"},
        },
        opset_version=17,
        dynamo=False,
    )
    print(f"  craft.onnx: {craft_path.stat().st_size / 1e6:.1f} МБ")

    # ── Распознаватель (VGG+BiLSTM+CTC) ─────────────────────────────
    rec = reader.recognizer
    if isinstance(rec, torch.nn.DataParallel):
        rec = rec.module
    rec.eval()

    model_height = int(getattr(reader, "model_height", 64))

    # AdaptiveAvgPool2d((None, 1)) не экспортируется в ONNX с динамическими
    # осями. При фиксированной высоте входа (imgH) высота фичемапы constant,
    # и пулинг (None,1) по последней оси ≡ mean(dim=3). Подменяем модуль —
    # математически эквивалентно (проверяется паритетом ниже).
    class MeanPoolLast(torch.nn.Module):
        def forward(self, x):
            return x.mean(dim=3, keepdim=True)

    if hasattr(rec, "AdaptiveAvgPool"):
        rec.AdaptiveAvgPool = MeanPoolLast()

    class RecWrap(torch.nn.Module):
        """Model.forward(input, text): text не используется в CTC-ветке."""
        def __init__(self, m):
            super().__init__()
            self.m = m

        def forward(self, x):
            return self.m(x, None)

    wrap = RecWrap(rec).eval()
    dummy_rec = torch.zeros(1, 1, model_height, 320, dtype=torch.float32)
    rec_path = OUT_DIR / "recognizer.onnx"
    torch.onnx.export(
        wrap, dummy_rec, str(rec_path),
        input_names=["image"], output_names=["logits"],
        dynamic_axes={
            "image": {0: "batch", 3: "width"},
            "logits": {0: "batch", 1: "time"},
        },
        opset_version=17,
        dynamo=False,
    )
    print(f"  recognizer.onnx: {rec_path.stat().st_size / 1e6:.1f} МБ")

    # ── INT8 dynamic-квантизация распознавателя ─────────────────────
    from onnxruntime.quantization import QuantType, quantize_dynamic
    rec_int8_path = OUT_DIR / "recognizer.int8.onnx"
    quantize_dynamic(str(rec_path), str(rec_int8_path),
                     weight_type=QuantType.QUInt8)
    print(f"  recognizer.int8.onnx: {rec_int8_path.stat().st_size / 1e6:.1f} МБ")

    # ── Метаданные для рантайма ─────────────────────────────────────
    characters = list(reader.converter.character)
    meta = {
        "langs": langs,
        "model_height": model_height,
        "blank_index": 0,
        "characters": characters,
        "easyocr_version": getattr(easyocr, "__version__", "?"),
        "opset": 17,
    }
    (OUT_DIR / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  meta.json: {len(characters)} символов, imgH={model_height}")

    # ── Смоук: onnxruntime может открыть и прогнать обе модели ──────
    import numpy as np
    import onnxruntime as ort
    so = ort.SessionOptions()
    d = ort.InferenceSession(str(craft_path), so, providers=["CPUExecutionProvider"])
    y = d.run(None, {"image": np.zeros((1, 3, 224, 320), np.float32)})[0]
    print(f"  smoke craft: y{y.shape}")
    r = ort.InferenceSession(str(rec_int8_path), so, providers=["CPUExecutionProvider"])
    lg = r.run(None, {"image": np.zeros((1, 1, model_height, 128), np.float32)})[0]
    print(f"  smoke recognizer(int8): logits{lg.shape}")

    # ── Паритет: torch (с подменённым пулингом) vs ONNX fp32 ────────
    rng = np.random.default_rng(7)
    sample = rng.standard_normal((1, 1, model_height, 200)).astype(np.float32)
    with torch.no_grad():
        ref = wrap(torch.from_numpy(sample)).numpy()
    rf = ort.InferenceSession(str(rec_path), so, providers=["CPUExecutionProvider"])
    got = rf.run(None, {"image": sample})[0]
    max_diff = float(np.abs(ref - got).max())
    argmax_match = float((ref.argmax(-1) == got.argmax(-1)).mean())
    print(f"  parity fp32: max|Δ|={max_diff:.2e}, argmax match={argmax_match:.1%}")
    assert argmax_match > 0.999, "ONNX-выход расходится с torch"
    print("EXPORT OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
