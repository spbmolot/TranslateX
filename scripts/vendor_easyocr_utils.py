"""
Генерирует core/ocr_backends/_easyocr_vendor.py — torch-free выдержку из
easyocr (Apache-2.0): постпроцессинг CRAFT (getDetBoxes и др.), препроцессинг
(resize_aspect_ratio, normalizeMeanVariance) и группировка боксов
(group_text_box, get_image_list).

Зачем vendored-копия: ONNX-бэкенду нельзя импортировать пакет easyocr —
его __init__ тянет torch (сотни МБ, DLL-ад). Нужные функции сами по себе
чистые numpy/cv2 — копируем их исходники дословно, с транзитивным
разрешением зависимостей внутри модулей easyocr.

Запуск (dev-only): python scripts/vendor_easyocr_utils.py
Перезапускать при обновлении easyocr.
"""
import ast
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import easyocr  # noqa: F401 — только в dev-окружении
import easyocr.craft_utils as cu
import easyocr.imgproc as ip
import easyocr.utils as u

OUT = Path(__file__).resolve().parent.parent / "core" / "ocr_backends" / "_easyocr_vendor.py"

# Стартовый набор — что реально нужно ONNX-бэкенду
SEED = [
    ip.resize_aspect_ratio,
    ip.normalizeMeanVariance,
    cu.getDetBoxes,
    cu.adjustResultCoordinates,
    u.group_text_box,
    u.get_image_list,
]
MODULES = {"easyocr.craft_utils": cu, "easyocr.imgproc": ip, "easyocr.utils": u}


def _names_used(src: str) -> set:
    tree = ast.parse(src)
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}


def collect(seed):
    """Транзитивно собрать функции из модулей easyocr, на которые ссылаются."""
    result = {}   # name -> (source, module_name)
    queue = [(f.__name__, f) for f in seed]
    while queue:
        name, fn = queue.pop()
        if name in result:
            continue
        src = inspect.getsource(fn)
        result[name] = (src, fn.__module__)
        for used in _names_used(src):
            if used in result:
                continue
            for mod in MODULES.values():
                cand = getattr(mod, used, None)
                if inspect.isfunction(cand) and cand.__module__ in MODULES:
                    queue.append((used, cand))
                    break
    return result


def main() -> int:
    collected = collect(SEED)
    version = getattr(easyocr, "__version__", "?")

    header = f'''"""
АВТОСГЕНЕРИРОВАНО scripts/vendor_easyocr_utils.py — НЕ ПРАВИТЬ РУКАМИ.

Функции из EasyOCR {version} (https://github.com/JaidedAI/EasyOCR), (c) JaidedAI,
Apache-2.0. Функции из craft_utils и imgproc происходят из CRAFT,
Copyright (c) 2019-present NAVER Corp., MIT. Тексты лицензий находятся в third_party/.
Функции скопированы, чтобы ONNX-бэкенд мог работать без импорта torch.
"""
import math

import cv2
import numpy as np
from PIL import Image  # noqa: F401 — используется utils.compute_ratio_and_resize
from scipy.ndimage import label  # noqa: F401 — используется craft_utils

'''
    # Стабильный порядок: по модулю, затем по имени
    parts = [header]
    for name in sorted(collected, key=lambda n: (collected[n][1], n)):
        src, mod = collected[name]
        parts.append(f"# ── из {mod} ──\n{src}\n")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    init = OUT.parent / "__init__.py"
    if not init.exists():
        init.write_text("", encoding="utf-8")
    OUT.write_text("\n".join(parts), encoding="utf-8")
    print(f"vendored {len(collected)} functions -> {OUT}")
    for name in sorted(collected):
        print(f"  {collected[name][1]}.{name}")

    # Смоук: модуль импортируется без torch
    import importlib.util
    spec = importlib.util.spec_from_file_location("_vendor_check", OUT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert "import torch" not in OUT.read_text(encoding="utf-8"), \
        "torch пролез в vendored код"
    print("import smoke OK, torch-free")
    return 0


if __name__ == "__main__":
    sys.exit(main())
