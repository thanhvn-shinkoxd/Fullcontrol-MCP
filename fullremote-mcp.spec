# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules, copy_metadata

root = Path(SPECPATH)
hiddenimports = collect_submodules("uvicorn") + ["anyio._backends._asyncio"]
for optional_module in ("pywinauto", "comtypes"):
    try:
        hiddenimports += collect_submodules(optional_module)
    except ImportError:
        # These Windows UI packages are optional at runtime.
        pass

analysis = Analysis(
    [str(root / "scripts" / "exe_entry.py")],
    pathex=[str(root / "src")],
    binaries=[],
    datas=copy_metadata("mcp"),
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
archive = PYZ(analysis.pure)
exe = EXE(
    archive,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="fullremote-mcp",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
