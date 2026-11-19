"""Preload the system MSVC runtime for PyInstaller's Windows import probes."""
import os
import sys
from pathlib import Path

if os.name == "nt" and Path(sys.argv[0]).as_posix().endswith("/isolated/_child.py"):
    import ctypes

    # PyInstaller probes Qt before torch. Its old Qt-local runtime is
    # incompatible with the current torch wheels if loaded first.
    system32 = Path(os.environ["SystemRoot"]) / "System32"
    for name in ("msvcp140", "msvcp140_1", "msvcp140_2",
                 "vcruntime140", "vcruntime140_1", "concrt140"):
        dll = system32 / (name + ".dll")
        if dll.exists():
            ctypes.WinDLL(str(dll))
