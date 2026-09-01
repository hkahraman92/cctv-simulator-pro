"""PyInstaller runtime hook: point Tcl/Tk at the bundled library directories.

PyInstaller ships its own tkinter runtime hook, but that hook is only wired in
when the module graph actually contains ``tkinter``. On some Windows/venv
layouts the graph misses the package while still picking up the ``_tkinter``
C extension, and then nothing sets ``TCL_LIBRARY`` / ``TK_LIBRARY``: Tcl falls
back to paths relative to the executable and startup dies with

    _tkinter.TclError: Can't find a usable init.tcl in the following directories

This hook runs before any application code and repairs that, using whatever
Tcl/Tk data actually shipped inside the bundle. It is a no-op when the
environment is already pointing somewhere valid, so it is safe to keep even
when PyInstaller's own hook did its job.
"""
import os
import sys


def _bundle_root():
    root = getattr(sys, "_MEIPASS", None)
    if root:
        return root
    return os.path.dirname(os.path.abspath(sys.executable))


def _is_valid(path, marker):
    return bool(path) and os.path.isfile(os.path.join(path, marker))


def _locate(root, marker, preferred, max_depth=3):
    """Directory inside `root` containing `marker`, cheap candidates first."""
    for name in preferred:
        candidate = os.path.join(root, *name.split("/"))
        if _is_valid(candidate, marker):
            return candidate
    base_depth = root.rstrip(os.sep).count(os.sep)
    for current, dirs, files in os.walk(root):
        if marker in files:
            return current
        if current.count(os.sep) - base_depth >= max_depth:
            dirs[:] = []          # do not descend further
    return None


def _configure():
    root = _bundle_root()
    if not root or not os.path.isdir(root):
        return

    if not _is_valid(os.environ.get("TCL_LIBRARY"), "init.tcl"):
        found = _locate(root, "init.tcl",
                        ("_tcl_data", "tcl", "tcl8.6", "tcl/tcl8.6", "lib/tcl8.6"))
        if found:
            os.environ["TCL_LIBRARY"] = found

    if not _is_valid(os.environ.get("TK_LIBRARY"), "tk.tcl"):
        found = _locate(root, "tk.tcl",
                        ("_tk_data", "tk", "tk8.6", "tcl/tk8.6", "lib/tk8.6"))
        if found:
            os.environ["TK_LIBRARY"] = found

    # Locate Tcl Modules (msgcat, etc.)
    for tm_dir in ("tcl8/8.5", "tcl8/8.6", "_tcl_data/tcl8/8.5", "_tcl_data/tcl8/8.6"):
        found_tm = _locate(root, "msgcat-1.6.1.tm", (tm_dir, "tcl8", "_tcl_data/tcl8"))
        if found_tm:
            parent_dir = found_tm if os.path.basename(found_tm) in ("8.5", "8.6") else os.path.join(found_tm, "8.5")
            if os.path.isdir(parent_dir):
                os.environ.setdefault("TCL8_5_TM_PATH", parent_dir)
                os.environ.setdefault("TCL8_6_TM_PATH", parent_dir)
            break

    if os.environ.get("CCTV_TCLTK_DEBUG"):
        sys.stderr.write(
            "[rth] root={0}\n[rth] TCL_LIBRARY={1}\n[rth] TK_LIBRARY={2}\n".format(
                root, os.environ.get("TCL_LIBRARY"), os.environ.get("TK_LIBRARY")))


_configure()
