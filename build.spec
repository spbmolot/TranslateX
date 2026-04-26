# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for TranslateX
# Run: pyinstaller build.spec
#
# After build, output is in dist/TranslateX/
# Use Inno Setup with installer/setup.iss to create the final .exe installer.

import sys
import os
from pathlib import Path

block_cipher = None
ROOT = Path(SPECPATH)

# ─── Data files to bundle ───────────────────────────────────────────────────
added_files = [
    (str(ROOT / "config.ini"),    "."),
    (str(ROOT / "assets"),        "assets"),
]

# EasyOCR detection models — included only if already downloaded
easyocr_model_dir = Path.home() / ".EasyOCR" / "model"
if easyocr_model_dir.exists():
    for model_file in easyocr_model_dir.iterdir():
        if model_file.is_file():
            added_files.append((str(model_file), "model"))

# ─── Hidden imports (PyQt5 + EasyOCR internals) ────────────────────────────
hidden_imports = [
    # Qt
    "PyQt5",
    "PyQt5.QtCore",
    "PyQt5.QtGui",
    "PyQt5.QtWidgets",
    "PyQt5.sip",
    # EasyOCR
    "easyocr",
    "easyocr.detection",
    "easyocr.recognition",
    "easyocr.utils",
    "easyocr.config",
    # Torch
    "torch",
    "torchvision",
    # PIL
    "PIL",
    "PIL.Image",
    # deep-translator (модуль google_trans переименован в google в новых версиях)
    "deep_translator",
    "deep_translator.google",
    # deepl
    "deepl",
    # mss
    "mss",
    "mss.windows",
    # pystray / keyboard
    "pystray",
    "keyboard",
    # stdlib
    "configparser",
    "logging",
    "collections",
    "json",
    "threading",
]

# ─── Analysis ────────────────────────────────────────────────────────────────
a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=added_files,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # GUI / научные библиотеки, не нужные для inference
        "tkinter",
        "matplotlib",
        "pandas",
        "IPython",
        "notebook",
        "jupyter",
        "pytest",
        # ВНИМАНИЕ: scipy И sklearn НЕ исключать — EasyOCR требует scipy.
        # ВНИМАНИЕ: torch.distributions/testing/onnx НЕ исключать —
        # их отсутствие ломает `import torch` в frozen-сборке.
        "torch.utils.tensorboard",
        "torchaudio",
        "skimage.io._plugins",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# ─── Фильтр бинарников: убираем дублирующиеся MSVC CRT из PyQt5/Qt5/bin ──────
# PyQt5 приносит свою старую версию MSVCP140.dll/VCRUNTIME140.dll (v14.26),
# которая конфликтует с новой версией от torch (v14.50).
# Если Qt-версия загружается первой, c10.dll в torch падает с WinError 1114
# (DllMain returned FALSE) — ключевая причина бага.
_DUP_CRT_NAMES = {
    "msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll",
    "msvcp140_1.dll", "msvcp140_2.dll",
}
def _strip_dup_crt(entries, label):
    out = []
    removed = []
    for entry in entries:
        dst = entry[0]
        name = os.path.basename(dst).lower()
        path_lower = dst.replace("\\", "/").lower()
        if name in _DUP_CRT_NAMES and "pyqt5/qt5/bin" in path_lower:
            removed.append(dst)
            continue
        out.append(entry)
    if removed:
        print(f"[build.spec] {label}: stripped {len(removed)} duplicate CRT DLL(s):")
        for r in removed:
            print(f"    — {r}")
    return out

a.binaries = _strip_dup_crt(a.binaries, "binaries")
a.datas    = _strip_dup_crt(a.datas,    "datas")

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TranslateX",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    console=False,          # No console window — GUI app
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "assets" / "icon.ico"),
    version="version_info.txt" if (ROOT / "version_info.txt").exists() else None,
)
# UPX отключён, потому что на больших torch-сборках он вешает PyInstaller (deadlock).

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="TranslateX",
)
