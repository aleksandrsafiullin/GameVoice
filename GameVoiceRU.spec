# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for GameVoice RU Windows build.

Build with:
    pyinstaller GameVoiceRU.spec --distpath dist --workpath build

The resulting GameVoiceRU.exe is a single-folder portable build.
Voice models (~60 MB each) are NOT bundled — they are downloaded on first run
to %USERPROFILE%\.gamevoice_ru\voices\ so the EXE stays small.
"""
import sys
from pathlib import Path

block_cipher = None

# Collect piper data files (espeak-ng-data)
try:
    import piper as _piper_pkg
    piper_dir = Path(_piper_pkg.__file__).parent
    piper_datas = [
        (str(piper_dir / "espeak-ng-data"), "piper/espeak-ng-data"),
    ]
except ImportError:
    piper_datas = []

a = Analysis(
    ["gamevoice_ru/__main__.py"],
    pathex=["."],
    binaries=[],
    datas=piper_datas + [
        ("docs/TZ.md", "docs"),
        ("DECISIONS.md", "."),
        ("README.ru.md", "."),
    ],
    hiddenimports=[
        "gamevoice_ru.pipeline.capture",
        "gamevoice_ru.pipeline.ocr",
        "gamevoice_ru.pipeline.stabilizer",
        "gamevoice_ru.pipeline.tts",
        "gamevoice_ru.pipeline.audio",
        "gamevoice_ru.pipeline.queue_mgr",
        "gamevoice_ru.pipeline.runner",
        "gamevoice_ru.self_test",
        "gamevoice_ru.ui.main_window",
        "gamevoice_ru.ui.setup_wizard",
        "gamevoice_ru.config",
        "piper",
        "piper.voice",
        "pytesseract",
        "numpy",
        "cv2",
        "sounddevice",
        "mss",
        "PIL",
        "tkinter",
        "tkinter.ttk",
        "tkinter.scrolledtext",
        "tkinter.filedialog",
        "tkinter.messagebox",
        # WinRT packages for Windows OCR (installed separately; bundled if present)
        "winrt",
        "winrt.windows.media.ocr",
        "winrt.windows.globalization",
        "winrt.windows.graphics.imaging",
        "winrt.windows.storage.streams",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "torch",         # Silero is optional
        "transformers",  # No translation models
        "matplotlib",
        "scipy",
        "pandas",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="GameVoiceRU",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # GUI app, no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="GameVoiceRU",
)
