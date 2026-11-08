# -*- mode: python ; coding: utf-8 -*-
"""cctv-core: the Python core as a Tk-free, console-subsystem onedir bundle.

The Tauri installer ships it as a resource folder (`core/`), so there is no
loose tree to lose in a zip copy (classic build failure 5) and no onefile
unpacking on every start. Console subsystem on purpose: the shell starts it
with CREATE_NO_WINDOW and talks over stdio pipes; a windowed bootloader may
hand Python no usable stdin/stdout.

    desktop\\packaging\\build_core.bat      -> desktop\\src-tauri\\core\\cctv-core.exe
"""
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

import os
import sys

ROOT = Path(SPECPATH).resolve().parents[1]          # repository root
# collect_* import the package in a child interpreter that inherits
# PYTHONPATH, not pathex; without this they silently find nothing.
sys.path.insert(0, str(ROOT))
os.environ["PYTHONPATH"] = str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", "")

# Every engine module, none of the Tk UI (the shell is the UI now).
hidden = [m for m in collect_submodules("cctv_simulator") if ".ui" not in m]
datas = collect_data_files("cctv_simulator", excludes=["**/__pycache__/**"])
if len(hidden) < 30 or not any(d[0].endswith(".json") for d in datas):
    raise SystemExit(f"cctv_simulator modulleri bulunamadi ({len(hidden)} modul) - ROOT={ROOT}")

# Local DEM GeoTIFFs: tifffile reads tiled / compressed (COG) files, imagecodecs
# decodes deflate / LZW / zstd / LERC and the floating-point predictor. Only
# those codecs ship: the full imagecodecs wheel is ~46 MB (AVIF alone 16 MB).
_KEEP_CODECS = {"_shared", "_shared_cython", "_imcd", "_imagecodecs", "_zlib", "_deflate", "_zlibng",
                "_lzma", "_zstd", "_lerc", "_jpeg8"}
codec_excludes = []
try:
    import imagecodecs as _ic
    hidden += ["tifffile", "imagecodecs"] + [f"imagecodecs.{m}" for m in sorted(_KEEP_CODECS)]
    codec_excludes = [f"imagecodecs.{f.stem.split('.')[0]}" for f in Path(_ic.__file__).parent.glob("_*.pyd")
                      if f.stem.split(".")[0] not in _KEEP_CODECS]
except ImportError:
    print("UYARI: imagecodecs yok - sikistirilmis GeoTIFF (COG) paketli cekirdekte okunamaz")

a = Analysis(
    [str(Path(SPECPATH) / "core_entry.py")],
    pathex=[str(ROOT)],
    hiddenimports=hidden,
    datas=datas,
    excludes=["tkinter", "_tkinter", "ttkbootstrap", "customtkinter", "matplotlib",
              "IPython", "pytest", "PyInstaller"] + codec_excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="cctv-core",
    console=True,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="core", upx=False)
