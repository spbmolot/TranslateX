"""
PERF-001: CLI бенчмарк-харнесса.

    python scripts/run_bench.py --baseline     # замерить и сохранить baseline.json
    python scripts/run_bench.py --compare      # замерить и показать diff с baseline
    python scripts/run_bench.py                # просто замерить и напечатать

Опции:
    --ocr       включить тяжёлый бенч EasyOCR readtext (нужны модели ~/.EasyOCR)
    --network   включить РЕАЛЬНЫЙ сетевой перевод (5 итераций; умеренно!)
"""
import argparse
import json
import platform
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Windows-консоль по умолчанию cp1251 — русский текст/юникод падает.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASELINE_PATH = Path(__file__).resolve().parent.parent / "tests" / "benchmark" / "baseline.json"


def run_all(include_ocr: bool, include_network: bool,
            include_onnx: bool = False) -> dict:
    # bench_core сам импортирует torch до PyQt5 (конфликт c10.dll/CRT)
    from tests.benchmark import bench_core
    results = {}
    for name, fn in bench_core.FAST_BENCHES.items():
        print(f"  {name}…", flush=True)
        results[name] = fn()
    results["ram_rss"] = bench_core.bench_ram()
    if include_ocr:
        print("  ocr_readtext_1440p (EasyOCR warm ×5, downscale=auto→720p)…", flush=True)
        results["ocr_readtext_1440p_auto"] = bench_core.bench_ocr_readtext(5, "auto")
        print("  ocr_readtext_1440p (downscale=off)…", flush=True)
        results["ocr_readtext_1440p_off"] = bench_core.bench_ocr_readtext(5, "off")
    if include_onnx:
        print("  onnx_readtext_1440p (downscale=auto→720p)…", flush=True)
        results["onnx_readtext_1440p_auto"] = bench_core.bench_onnx_readtext(5, "auto")
        print("  onnx_readtext_1440p (downscale=off)…", flush=True)
        results["onnx_readtext_1440p_off"] = bench_core.bench_onnx_readtext(5, "off")
    if include_network:
        print("  translation_miss_network (5 строк ×5, реальная сеть)…", flush=True)
        results["translation_miss_network_5"] = bench_core.bench_translation_miss_network(5)
    return results


def print_table(results: dict, baseline: dict | None = None) -> None:
    w = max(len(k) for k in results) + 2
    header = f"{'bench':<{w}}{'median':>10}{'p95':>10}"
    if baseline:
        header += f"{'base med':>10}{'Δ%':>8}"
    print("\n" + header)
    print("-" * len(header))
    for name, r in results.items():
        med = r.get("median_ms")
        if med is None:  # ram и др. не-временные метрики
            print(f"{name:<{w}}{json.dumps(r):>10}")
            continue
        line = f"{name:<{w}}{med:>9.2f}м{r['p95_ms']:>9.2f}м"
        if baseline and name in baseline and "median_ms" in baseline[name]:
            base = baseline[name]["median_ms"]
            delta = ((med - base) / base * 100.0) if base else 0.0
            line += f"{base:>9.2f}м{delta:>+7.1f}%"
        print(line)


def main() -> int:
    ap = argparse.ArgumentParser(description="TranslateX performance harness")
    ap.add_argument("--baseline", action="store_true",
                    help="сохранить результаты как baseline.json")
    ap.add_argument("--compare", action="store_true",
                    help="сравнить с сохранённым baseline.json")
    ap.add_argument("--ocr", action="store_true", help="включить бенч EasyOCR")
    ap.add_argument("--onnx", action="store_true",
                    help="включить бенч ONNX-бэкенда (нужны модели export_onnx.py)")
    ap.add_argument("--network", action="store_true",
                    help="включить сетевой бенч перевода")
    args = ap.parse_args()

    print("Запуск бенчей…")
    results = run_all(args.ocr, args.network, args.onnx)

    baseline = None
    if args.compare:
        if not BASELINE_PATH.exists():
            print(f"Baseline не найден: {BASELINE_PATH} — сначала --baseline")
            return 1
        baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))["results"]

    print_table(results, baseline)

    if args.baseline:
        payload = {
            "date": date.today().isoformat(),
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "results": results,
        }
        BASELINE_PATH.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nBaseline сохранён: {BASELINE_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
