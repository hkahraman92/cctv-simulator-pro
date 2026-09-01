# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller build for the full CCTV simulator.

Bundles the classic dual-view application, the 3D camera-eye view, the camera
database screen, the specification assistant, all exporters, and the modern
EN 62676-4 optics workbench, into one executable.

Build (from this folder):

    build_exe.bat                 -> dist\\CCTV Simulator\\CCTV Simulator.exe   (folder, fast start)
    build_exe.bat onefile         -> dist\\CCTV Simulator.exe                   (single file)

or directly:

    pyinstaller --noconfirm cctv_simulator.spec
    set CCTV_ONEFILE=1 && pyinstaller --noconfirm cctv_simulator.spec
"""
import glob
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# ── preflight: the whole UI is Tk, so refuse to build a broken exe ───────────
# If the *building* interpreter has no tkinter, PyInstaller silently ships an
# executable that dies at startup with "No module named 'tkinter'".
try:
    import tkinter          # noqa: F401
    import _tkinter         # noqa: F401
except Exception as exc:    # pragma: no cover - build-time guard
    raise SystemExit(
        "\n"
        "==========================================================\n"
        "  BUILD DURDURULDU: bu Python'da tkinter yok\n"
        "==========================================================\n"
        f"  Yorumlayici : {sys.executable}\n"
        f"  Hata        : {exc}\n"
        "\n"
        "  Tum arayuz Tk uzerine kurulu. tkinter olmadan uretilen exe\n"
        "  acilista 'No module named tkinter' verir.\n"
        "\n"
        "  Cozum: python.org kurulumunu Modify ile acip\n"
        "         'tcl/tk and IDLE' secenegini isaretleyin,\n"
        "         sonra .venv-build klasorunu silip tekrar derleyin.\n"
        "==========================================================\n"
    )

# Where the pure-Python half of tkinter lives in THIS interpreter. Shipped as
# a file tree below, as insurance: on some Windows/venv layouts PyInstaller
# bundles the _tkinter C extension but misses the tkinter package itself.
TKINTER_DIR = os.path.dirname(os.path.abspath(tkinter.__file__))

# Where THIS interpreter keeps its Tcl/Tk script libraries and DLLs.
#
# tkinter.Tcl() is the obvious probe (and the one PyInstaller's own
# hook-_tkinter uses), but inside a Windows venv it can fail outright: the
# interpreter imports tkinter fine, yet creating a Tcl interpreter dies
# because TCL_LIBRARY is unset and Tcl looks next to .venv\Scripts\python.exe.
# When that happens PyInstaller silently ships no Tcl/Tk data at all. So try
# the probe, then fall back to looking on disk.
TK_VER = "8.6"
try:
    TK_VER = "{0:.1f}".format(tkinter.TkVersion)   # constant, needs no Tcl
except Exception:
    pass


def _search_roots():
    roots = []
    for root in (os.environ.get("TCL_LIBRARY"), os.environ.get("TK_LIBRARY"),
                 sys.base_prefix, sys.prefix,
                 os.path.dirname(os.path.dirname(TKINTER_DIR))):
        if root and os.path.isdir(root) and root not in roots:
            roots.append(root)
    return roots


def _find_script_dir(marker, names):
    # the env var may already point straight at it
    for env in ("TCL_LIBRARY", "TK_LIBRARY"):
        value = os.environ.get(env)
        if value and os.path.isfile(os.path.join(value, marker)):
            return value
    patterns = []
    for name in names:
        patterns += ["tcl/{0}*".format(name), "{0}*".format(name),
                     "lib/{0}*".format(name), "Library/lib/{0}*".format(name),
                     "share/tcltk/{0}*".format(name), "tcl/lib/{0}*".format(name)]
    for root in _search_roots():
        for pattern in patterns:
            for candidate in sorted(glob.glob(os.path.join(root, *pattern.split("/")))):
                if os.path.isfile(os.path.join(candidate, marker)):
                    return candidate
    return None


def _probe_tcltk_dirs():
    tcl_dir = tk_dir = None
    try:                                   # cheapest path, when it works
        probed = tkinter.Tcl().eval("info library")
        if os.path.isfile(os.path.join(probed, "init.tcl")):
            tcl_dir = probed
            sibling = os.path.join(os.path.dirname(probed), "tk" + TK_VER)
            if os.path.isfile(os.path.join(sibling, "tk.tcl")):
                tk_dir = sibling
    except Exception:
        pass
    if tcl_dir is None:
        tcl_dir = _find_script_dir("init.tcl", ["tcl" + TK_VER, "tcl8.", "tcl"])
    if tk_dir is None:
        tk_dir = _find_script_dir("tk.tcl", ["tk" + TK_VER, "tk8.", "tk"])
    return tcl_dir, tk_dir


def _probe_tcltk_dlls():
    """tcl86t.dll / tk86t.dll next to the interpreter (Windows)."""
    found = []
    if not sys.platform.startswith("win"):
        return found
    seen = set()
    for root in _search_roots():
        for sub in ("DLLs", "Library/bin", ""):
            folder = os.path.join(root, *sub.split("/")) if sub else root
            for pattern in ("tcl8*.dll", "tk8*.dll", "zlib1.dll"):
                for path in glob.glob(os.path.join(folder, pattern)):
                    name = os.path.basename(path).lower()
                    if name not in seen:
                        seen.add(name)
                        found.append((path, "."))
    return found


def _probe_tcl_modules(tcl_dir):
    """Directory holding the Tcl Modules (`*.tm`), msgcat among them.

    msgcat is NOT a package inside tcl8.6/ - it is a Tcl Module shipped as
    tcl8/<ver>/msgcat-*.tm. ttkbootstrap calls ::msgcat::mcmset while building
    its theme, so a bundle without it dies with

        invalid command name "::msgcat::mcmset"

    On Linux the module dir is nested INSIDE tcl8.6/, so bundling tcl8.6 picks
    it up for free. On Windows (python.org layout) tcl8/ is a SIBLING of
    tcl8.6/ under <Python>/tcl/, so bundling tcl8.6 alone silently loses it.
    """
    if not tcl_dir:
        return None, False
    nested = os.path.join(tcl_dir, "tcl8")
    if glob.glob(os.path.join(nested, "**", "msgcat*.tm"), recursive=True):
        return nested, True            # already inside TCL_DIR, nothing to add
    for candidate in (os.path.join(os.path.dirname(tcl_dir), "tcl8"),
                      os.path.join(os.path.dirname(os.path.dirname(tcl_dir)), "tcl8")):
        if glob.glob(os.path.join(candidate, "**", "msgcat*.tm"), recursive=True):
            return candidate, False    # sibling: must be bundled explicitly
    for root in _search_roots():
        for hit in glob.glob(os.path.join(root, "**", "msgcat*.tm"), recursive=True):
            module_root = os.path.dirname(os.path.dirname(hit))
            if os.path.basename(module_root) == "tcl8":
                return module_root, False
            return os.path.dirname(hit), False
    return None, False


TCL_DIR, TK_DIR = _probe_tcltk_dirs()
TCL_MODULES_DIR, TCL_MODULES_NESTED = _probe_tcl_modules(TCL_DIR)
TCLTK_DLLS = _probe_tcltk_dlls()

ONEFILE = os.environ.get("CCTV_ONEFILE", "0") == "1"
APP_NAME = "CCTV Simulator"
ROOT = Path(SPECPATH)

# ── data files ───────────────────────────────────────────────────────────────
datas = [
    ("camera_library_from_excel.json", "."),
    ("assets", "assets"),
    ("cctv_simulator", "cctv_simulator"),
]
if (ROOT / "README.md").exists():
    datas.append(("README.md", "."))

# ttkbootstrap ships its themes as JSON, customtkinter ships themes + assets,
# reportlab ships its font metrics - none of these are importable modules, so
# PyInstaller cannot find them by following imports.
for package in ("ttkbootstrap", "customtkinter", "reportlab"):
    try:
        datas += collect_data_files(package)
    except Exception:
        pass

# ── hidden imports ───────────────────────────────────────────────────────────
hiddenimports = [
    # CCTV Simulator core and UI submodules
    "cctv_simulator",
    "cctv_simulator.config",
    "cctv_simulator.database",
    "cctv_simulator.errors",
    "cctv_simulator.exporters",
    "cctv_simulator.models",
    "cctv_simulator.theme",
    "cctv_simulator.calculations",
    "cctv_simulator.terrain_loader",
    "cctv_simulator.viewshed_3d",
    "cctv_simulator.perimeter_planner",
    "cctv_simulator.online_map_loader",
    "cctv_simulator.ui",
    "cctv_simulator.ui.main_window",
    "cctv_simulator.ui.map_3d_window",
    "cctv_simulator.ui.view_3d_window",
    "cctv_simulator.ui.canvas_drawer",
    "cctv_simulator.ui.camera_db_window",
    "cctv_simulator.ui.spec_assistant",
    "cctv_simulator.ui.modern_window",
    # Tk: named explicitly so a missing submodule can never be optimised away
    "tkinter", "tkinter.ttk", "tkinter.filedialog", "tkinter.messagebox",
    "tkinter.colorchooser", "tkinter.font", "tkinter.simpledialog",
    "_tkinter",
    "PIL._tkinter_finder",      # PIL.ImageTk needs this at runtime
    "PIL.ImageGrab",            # used by export_png
    "openpyxl.cell._writer",    # openpyxl lazy-imports this
]
for package in ("cctv_simulator", "ttkbootstrap", "customtkinter", "reportlab", "openpyxl"):
    try:
        hiddenimports += collect_submodules(package)
    except Exception:
        pass

# ── keep the build lean ──────────────────────────────────────────────────────
excludes = [
    "matplotlib", "scipy", "pandas", "IPython", "notebook",
    "pytest", "sphinx",
    # NOTE: nothing under "tkinter" is excluded, and neither "test" nor
    # "unittest" - several bundled libraries import them at runtime.
]

icon_path = ROOT / "assets" / "cctv_logo.ico"
icon = str(icon_path) if icon_path.exists() else None

a = Analysis(
    ["cctv_dual_view_simulator.py"],
    pathex=[str(ROOT)],
    binaries=TCLTK_DLLS,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(ROOT / "runtime_hook_tcltk.py")],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
# ── post-analysis report ─────────────────────────────────────────────────────
_modules = sorted(name for name, _p, _k in a.pure if name == "tkinter" or name.startswith("tkinter."))
_ext = sorted(os.path.basename(name) for name, _p, _k in a.binaries
              if os.path.basename(name).startswith("_tkinter"))

def _shipped(prefix, marker):
    needle = "{0}/{1}".format(prefix, marker)
    return any(name.replace("\\", "/").endswith(needle) for name, _p, _k in a.datas)

_report = [
    "PyInstaller tkinter toplama raporu",
    "=" * 58,
    f"Yorumlayici        : {sys.executable}",
    f"tkinter paketi     : {TKINTER_DIR}",
    f"a.pure icindekiler : {len(_modules)} modul",
    *(f"    {name}" for name in _modules),
    f"a.binaries uzanti  : {_ext or 'YOK'}",
    f"Tk surumu          : {TK_VER}",
    f"Aranan kokler      : {_search_roots()}",
    f"Tcl kutuphanesi    : {TCL_DIR}",
    f"Tk  kutuphanesi    : {TK_DIR}",
    f"Tcl/Tk DLL         : {[os.path.basename(p_) for p_, _d in TCLTK_DLLS] or 'YOK'}",
    f"Tcl modul dizini   : {TCL_MODULES_DIR}",
    f"  tcl8.6 icinde mi : {TCL_MODULES_NESTED}",
    f"_tcl_data pakette  : {_shipped('_tcl_data', 'init.tcl')}",
    f"_tk_data  pakette  : {_shipped('_tk_data', 'tk.tcl')}",
]
_diag = Path("build") / "tkinter-diagnostic.txt"
try:
    _diag.parent.mkdir(parents=True, exist_ok=True)
    _diag.write_text("\n".join(_report) + "\n", encoding="utf-8")
except Exception:
    pass

# The C extension cannot be substituted - without it there is no Tk at all.
if not _ext:
    raise SystemExit(
        "\n"
        "==========================================================\n"
        "  BUILD DURDURULDU: _tkinter C uzantisi bulunamadi\n"
        "==========================================================\n"
        f"  Yorumlayici : {sys.executable}\n"
        "\n"
        "  Bu Python kurulumunda Tk yok. python.org kurulumunu\n"
        "  Modify ile acip 'tcl/tk and IDLE' secenegini isaretleyin,\n"
        "  .venv-build klasorunu silip tekrar derleyin.\n"
        "==========================================================\n"
    )

# ── Tcl/Tk script libraries ─────────────────────────────────────────────────
# Without init.tcl / tk.tcl the exe dies at Tk() with
# "Can't find a usable init.tcl". PyInstaller's _tkinter hook normally brings
# them in as _tcl_data / _tk_data; add them ourselves when it did not.
extra_trees = []
if not _shipped("_tcl_data", "init.tcl"):
    if TCL_DIR:
        extra_trees.append(Tree(TCL_DIR, prefix="_tcl_data"))
        print("[spec] Tcl kutuphanesi elle eklendi:", TCL_DIR)
    else:
        raise SystemExit(
            "\n"
            "==========================================================\n"
            "  BUILD DURDURULDU: Tcl betik kutuphanesi (init.tcl) yok\n"
            "==========================================================\n"
            f"  Yorumlayici : {sys.executable}\n"
            f"  Arananlar   : {_search_roots()}\n"
            "\n"
            "  Beklenen yer (python.org kurulumu):\n"
            "     <Python313>\\tcl\\tcl{TK_VER}\\init.tcl\n"
            "\n"
            "  Bu klasor yoksa Tk kurulu degil: python.org kurulumunu\n"
            "  Modify ile acip 'tcl/tk and IDLE' secenegini isaretleyin,\n"
            "  .venv-build klasorunu silip tekrar derleyin.\n"
            "==========================================================\n"
        )
if not _shipped("_tk_data", "tk.tcl"):
    if TK_DIR:
        extra_trees.append(Tree(TK_DIR, prefix="_tk_data"))
        print("[spec] Tk kutuphanesi elle eklendi:", TK_DIR)
    else:
        raise SystemExit(
            "\n"
            "==========================================================\n"
            "  BUILD DURDURULDU: Tk betik kutuphanesi (tk.tcl) yok\n"
            "==========================================================\n"
            f"  Yorumlayici : {sys.executable}\n"
            f"  Arananlar   : {_search_roots()}\n"
            "\n"
            "  Beklenen yer (python.org kurulumu):\n"
            "     <Python313>\\tcl\\tk{TK_VER}\\tk.tcl\n"
            "\n"
            "  Bu klasor yoksa Tk kurulu degil: python.org kurulumunu\n"
            "  Modify ile acip 'tcl/tk and IDLE' secenegini isaretleyin,\n"
            "  .venv-build klasorunu silip tekrar derleyin.\n"
            "==========================================================\n"
        )

# msgcat and the rest of the Tcl Modules. Placed at both tcl8 and _tcl_data/tcl8
if TCL_MODULES_DIR and not TCL_MODULES_NESTED:
    extra_trees.append(Tree(TCL_MODULES_DIR, prefix="tcl8"))
    extra_trees.append(Tree(TCL_MODULES_DIR, prefix="_tcl_data/tcl8"))
    print("[spec] Tcl modulleri (msgcat dahil) eklendi:", TCL_MODULES_DIR)
elif not TCL_MODULES_DIR:
    raise SystemExit(
        "\n"
        "==========================================================\n"
        "  BUILD DURDURULDU: msgcat Tcl modulu bulunamadi\n"
        "==========================================================\n"
        f"  Tcl kutuphanesi : {TCL_DIR}\n"
        f"  Arananlar       : {_search_roots()}\n"
        "\n"
        "  ttkbootstrap tema kurarken ::msgcat::mcmset cagirir.\n"
        "  msgcat pakete girmezse exe acilista\n"
        "  'invalid command name \"::msgcat::mcmset\"' verir.\n"
        "\n"
        "  Beklenen yer: <Python313>\\tcl\\tcl8\\8.6\\msgcat-*.tm\n"
        "==========================================================\n"
    )

# The pure-Python half is shipped as a file tree regardless of what the module
# graph decided. PyInstaller's importer reads the PYZ first, so this copy is
# only ever used when the graph missed the package - which is exactly the
# failure it exists to cover. Costs ~400 KB.
tkinter_tree = Tree(TKINTER_DIR, prefix="tkinter",
                    excludes=["test", "__pycache__", "*.pyc"])

if _modules:
    print(f"[spec] tkinter OK - {len(_modules)} modul PYZ icinde, "
          f"uzanti {_ext[0]}, ayrica dosya agaci da eklendi.")
else:
    print("[spec] UYARI: modul grafigi tkinter paketini kacirdi; "
          f"paket dosya agaci olarak ekleniyor ({TKINTER_DIR}).")

pyz = PYZ(a.pure)

if ONEFILE:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        tkinter_tree,
        *extra_trees,
        [],
        name=APP_NAME,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,              # UPX trips several antivirus engines
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=icon,
    )
else:
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
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=icon,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        tkinter_tree,
        *extra_trees,
        strip=False,
        upx=False,
        upx_exclude=[],
        name=APP_NAME,
    )
