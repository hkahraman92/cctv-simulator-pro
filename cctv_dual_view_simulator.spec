from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = [("camera_library_from_excel.json", "."), ("assets", "assets")] + collect_data_files("ttkbootstrap") + collect_data_files("reportlab")
hiddenimports = collect_submodules("ttkbootstrap") + collect_submodules("reportlab")

a = Analysis(
    ["cctv_dual_view_simulator.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CCTV Dual View Simulator",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="CCTV Dual View Simulator",
)
