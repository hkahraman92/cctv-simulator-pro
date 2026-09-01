# -*- mode: python ; coding: utf-8 -*-
"""
Minimal baseline spec. No Tcl/Tk workarounds of any kind.

PyInstaller handles tkinter correctly on its own when the building interpreter
is healthy. Use this first. If the resulting exe starts, the previous
"tkinter / init.tcl / msgcat / pyimage" failures were all downstream symptoms
of hand-placing Tcl/Tk, and none of that machinery is needed.

If this build produces an exe that still cannot import tkinter, the problem is
the build environment, not the spec, and no spec will fix it. Check:

    py -3.13 -c "import tkinter, sys; print(sys.executable, tkinter.TkVersion)"
    dir "%LOCALAPPDATA%\\Programs\\Python\\Python313\\tcl"

Build:
    rmdir /s /q .venv-build build dist
    py -3.13 -m venv .venv-build
    .venv-build\\Scripts\\python -m pip install -r requirements.txt
    .venv-build\\Scripts\\python -m PyInstaller --noconfirm --clean cctv_simulator_minimal.spec

Then confirm tkinter actually landed:
    dir "dist\\CCTV Simulator\\_internal\\base_library.zip"
    .venv-build\\Scripts\\python -c "import zipfile;print([n for n in zipfile.ZipFile(r'dist/CCTV Simulator/_internal/base_library.zip').namelist() if n.startswith('tkinter')][:5])"
"""
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

APP_NAME = "CCTV Simulator"
ROOT = Path(SPECPATH)

datas = [
    ("camera_library_from_excel.json", "."),
    ("assets", "assets"),
]
hiddenimports = [
    "PIL._tkinter_finder",
    "PIL.ImageGrab",
    "openpyxl.cell._writer",
]
# Themes, assets and font metrics are data files, not importable modules, so
# the module graph cannot find them on its own. This is the only collection
# this spec does.
for package in ("ttkbootstrap", "customtkinter", "reportlab"):
    try:
        datas += collect_data_files(package)
        hiddenimports += collect_submodules(package)
    except Exception:
        pass

icon_path = ROOT / "assets" / "cctv_logo.ico"

a = Analysis(
    ["cctv_dual_view_simulator.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["matplotlib", "scipy", "pandas", "IPython", "notebook"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # Console ON until the build is proven. Every traceback then lands in a
    # window you can read, instead of a null stderr. Flip to False at the end.
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon_path) if icon_path.exists() else None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)
