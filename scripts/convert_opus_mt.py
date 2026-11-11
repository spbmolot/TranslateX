"""
Подготовка быстрой модели перевода (провайдер local_fast) для публикации.

Конвертирует Helsinki-NLP/opus-mt-<src>-<tgt> в CTranslate2 int8 и собирает
zip, который приложение скачивает по требованию (см. OPUS_URL_TEMPLATE в
core/models.py). Нужны dev-зависимости: torch, transformers, ctranslate2.

    python scripts/convert_opus_mt.py en ru            # → dist_models/opus-mt-en-ru-ct2-int8.zip
    python scripts/convert_opus_mt.py en ru --install  # и сразу поставить в папку моделей
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

KEEP = ("model.bin", "config.json", "shared_vocabulary.json", "source.spm", "target.spm")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("tgt")
    ap.add_argument("--out", default=str(ROOT / "dist_models"))
    ap.add_argument("--install", action="store_true",
                    help="скопировать в папку моделей приложения")
    args = ap.parse_args()

    pair = f"{args.src}-{args.tgt}"
    out_root = Path(args.out)
    work = out_root / f"opus-mt-{pair}"
    out_root.mkdir(parents=True, exist_ok=True)

    subprocess.run(
        [sys.executable, "-m", "ctranslate2.converters.transformers",
         "--model", f"Helsinki-NLP/opus-mt-{pair}", "--output_dir", str(work),
         "--quantization", "int8", "--copy_files", "source.spm", "target.spm",
         "--force"],
        check=True)

    archive = out_root / f"opus-mt-{pair}-ct2-int8.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for name in KEEP:
            z.write(work / name, name)
    print(f"Готово: {archive} ({archive.stat().st_size / 1e6:.0f} МБ)")

    if args.install:
        from core import models
        target = models.opus_model(args.src, args.tgt).directory()
        target.mkdir(parents=True, exist_ok=True)
        for name in KEEP:
            shutil.copy2(work / name, target / name)
        print(f"Установлено в {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
