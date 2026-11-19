"""
Single-source-of-truth для версии TranslateX.

Это ЕДИНСТВЕННОЕ место, где хранится номер версии. Изменяешь здесь —
дальше:
  • UI (tray tooltip, About) импортирует напрямую: from core.__version__ import __version__
  • PyInstaller version_info.txt — генерируется `scripts/sync_version.py`
  • Inno Setup setup.iss — сверяет с версией упакованного EXE

После изменения версии запусти:
    python scripts/sync_version.py
"""
from __future__ import annotations

__version__: str = "1.0.2"
__version_tuple__: tuple[int, int, int, int] = (1, 0, 2, 0)


def parse_version(v: str) -> tuple[int, int, int, int]:
    """Парсит "X.Y.Z" → (X, Y, Z, 0). Лишние компоненты игнорируются."""
    parts = [int(x) for x in v.split(".") if x.isdigit()][:4]
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts)  # type: ignore[return-value]


# Self-check на согласованность строки и кортежа.
assert parse_version(__version__) == __version_tuple__, (
    f"version mismatch: __version__={__version__!r} vs "
    f"__version_tuple__={__version_tuple__!r}"
)
