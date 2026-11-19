"""
Определение видеокарты для локальной LLM: есть ли GPU, чей он и сколько
на нём памяти. По этому выбирается сборка llama.cpp: CUDA (NVIDIA),
Vulkan (AMD/Intel) или процессор.
"""
from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)

# Модель ~2,5 ГБ + контекст: на карту с меньшей памятью она не поместится.
MIN_VRAM_MB = 3800

_NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW: без мигающего окна консоли


@dataclass(frozen=True)
class GpuInfo:
    name: str
    vendor: str      # nvidia | amd | intel | other
    vram_mb: int

    @property
    def usable(self) -> bool:
        return self.vram_mb >= MIN_VRAM_MB


def _vendor(name: str) -> str:
    low = name.lower()
    if "nvidia" in low or "geforce" in low or "rtx" in low or "quadro" in low:
        return "nvidia"
    if "amd" in low or "radeon" in low:
        return "amd"
    if "intel" in low:
        return "intel"
    return "other"


def _nvidia_gpus() -> List[GpuInfo]:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
            creationflags=_NO_WINDOW if os.name == "nt" else 0,
        )
        if out.returncode != 0:
            return []
        gpus = []
        for line in out.stdout.strip().splitlines():
            name, mem = [p.strip() for p in line.rsplit(",", 1)]
            gpus.append(GpuInfo(name, "nvidia", int(float(mem))))
        return gpus
    except (OSError, ValueError, subprocess.SubprocessError):
        return []


def _registry_gpus() -> List[GpuInfo]:
    """Все видеоадаптеры из реестра. qwMemorySize — 64-битный объём
    выделенной видеопамяти (WMI AdapterRAM обрезается на 4 ГБ)."""
    if os.name != "nt":
        return []
    import winreg
    base = (r"SYSTEM\CurrentControlSet\Control\Class"
            r"\{4d36e968-e325-11ce-bfc1-08002be10318}")
    gpus: List[GpuInfo] = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as root:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(root, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(root, sub) as key:
                        name = winreg.QueryValueEx(key, "DriverDesc")[0]
                        try:
                            mem = winreg.QueryValueEx(
                                key, "HardwareInformation.qwMemorySize")[0]
                        except OSError:
                            mem = winreg.QueryValueEx(
                                key, "HardwareInformation.MemorySize")[0]
                        if isinstance(mem, bytes):
                            mem = int.from_bytes(mem, "little")
                        gpus.append(GpuInfo(str(name), _vendor(str(name)),
                                            int(mem) // (1024 * 1024)))
                except OSError:
                    continue
    except OSError:
        pass
    return gpus


def detect_gpu() -> Optional[GpuInfo]:
    """Самая ёмкая видеокарта системы или None, если адаптеров не найдено."""
    gpus = _nvidia_gpus() or _registry_gpus()
    if not gpus:
        return None
    best = max(gpus, key=lambda g: g.vram_mb)
    logger.debug(f"GPU: {best}")
    return best


def llm_variants(device: str, gpu: Optional[GpuInfo]) -> List[str]:
    """
    Сборки llama.cpp в порядке предпочтения для настройки device:
      auto — GPU, если он подходит, иначе процессор;
      gpu  — GPU принудительно (даже если памяти мало), с откатом на процессор;
      cpu  — только процессор.
    Последним всегда идёт 'cpu' — на него откатываемся, если GPU-сборка
    не установлена или не запустилась.
    """
    if device == "cpu" or gpu is None:
        return ["cpu"]
    if device == "auto" and not gpu.usable:
        return ["cpu"]
    first = "cuda" if gpu.vendor == "nvidia" else "vulkan"
    # У NVIDIA Vulkan — рабочая альтернатива, если CUDA-сборка не скачана.
    order = [first, "vulkan", "cpu"] if first == "cuda" else [first, "cpu"]
    return order


def describe(gpu: Optional[GpuInfo]) -> str:
    if gpu is None:
        return "видеокарта не найдена"
    mem = f"{gpu.vram_mb / 1024:.0f} ГБ" if gpu.vram_mb >= 1024 else f"{gpu.vram_mb} МБ"
    note = "" if gpu.usable else " — мало памяти для LLM"
    return f"{gpu.name}, {mem}{note}"
