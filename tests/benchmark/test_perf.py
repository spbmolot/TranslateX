"""
PERF-001: pytest-обёртка над бенчами. НЕ гоняется в обычном `pytest tests`
(см. pytest.ini: addopts = -m "not perf"). Запуск:

    pytest -m perf -s tests/benchmark

Замеры печатаются в stdout; сравнение с baseline — scripts/run_bench.py.
"""
import pytest

from tests.benchmark import bench_core

pytestmark = pytest.mark.perf


@pytest.mark.parametrize("name", sorted(bench_core.FAST_BENCHES))
def test_fast_bench(name):
    result = bench_core.FAST_BENCHES[name]()
    assert result["median_ms"] >= 0
    assert result["p95_ms"] >= result["median_ms"]
    print(f"\n{name}: median={result['median_ms']}ms p95={result['p95_ms']}ms")


def test_ram():
    result = bench_core.bench_ram()
    assert result["rss_mb"] > 0
    print(f"\nRAM rss: {result['rss_mb']} MB")
