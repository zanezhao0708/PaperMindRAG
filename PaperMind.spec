# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


ROOT = Path(SPECPATH)

a = Analysis(
    [str(ROOT / "desktop.pyw")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[(str(ROOT / "templates"), "templates")],
    hiddenimports=["webview.platforms.winforms", "webview.platforms.edgechromium"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PyQt5", "PyQt6", "PySide2", "PySide6", "cefpython3", "gi"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="PaperMind",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    icon=str(ROOT / "assets" / "papermind.ico"),
    codesign_identity=None,
    entitlements_file=None,
)
