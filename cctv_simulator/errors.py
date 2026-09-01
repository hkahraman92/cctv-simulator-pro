"""Make failures visible.

In a windowed PyInstaller build (`console=False`) stderr goes to a null device.
Tk catches every exception raised inside a widget callback in
`Tk.report_callback_exception`, whose default implementation prints the
traceback to that null stderr. The result is a button that appears to do
nothing, or a child window that opens empty and cannot be closed, with no
diagnostic anywhere.

`install_error_reporting()` routes both the interpreter hook and the Tk callback
hook to a log file next to the executable and to a dialog, so a failure names
itself instead of looking like a frozen UI.
"""
import sys
import traceback
from datetime import datetime
from pathlib import Path

_LOG_NAME = "cctv-simulator-error.log"
_MAX_DIALOGS = 3          # one dialog per distinct failure, then log only
_seen: set = set()
_dialog_count = 0


def log_path() -> Path:
    """Beside the executable when frozen, else beside the package."""
    from .config import is_frozen_app, user_data_dir
    if is_frozen_app():
        base = Path(sys.executable).resolve().parent
        try:                                   # Program Files is not writable
            probe = base / ".write-test"
            probe.touch()
            probe.unlink()
        except Exception:
            base = user_data_dir()
            base.mkdir(parents=True, exist_ok=True)
    else:
        base = Path(__file__).resolve().parent.parent
    return base / _LOG_NAME


def record(exc_type, exc_value, exc_tb, context: str = "") -> str:
    """Append a traceback to the log. Returns the formatted text."""
    text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header = f"\n{'=' * 70}\n{stamp}"
    if context:
        header += f"  [{context}]"
    header += f"\nPython   : {sys.version.split()[0]}\nExecutable: {sys.executable}\n{'-' * 70}\n"
    try:
        path = log_path()
        with path.open("a", encoding="utf-8") as handle:
            handle.write(header + text)
    except Exception:
        pass
    return text


def note(text: str, context: str = ""):
    """Write an informational block to the log. No dialog, no traceback."""
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header = f"\n{'=' * 70}\n{stamp}  [{context or 'bilgi'}]\n{'-' * 70}\n"
    try:
        with log_path().open("a", encoding="utf-8") as handle:
            handle.write(header + text.rstrip() + "\n")
    except Exception:
        pass


def _show(text: str, context: str = ""):
    global _dialog_count
    key = text.strip().splitlines()[-1] if text.strip() else context
    if key in _seen or _dialog_count >= _MAX_DIALOGS:
        return
    _seen.add(key)
    _dialog_count += 1
    try:
        from tkinter import messagebox
        where = context or "Uygulama"
        messagebox.showerror(
            f"Hata: {where}",
            f"{text.strip().splitlines()[-1]}\n\n"
            f"Ayrıntılı kayıt:\n{log_path()}",
        )
    except Exception:
        pass


def report(exc: BaseException, context: str = "", show: bool = True):
    """Log one caught exception, optionally showing it."""
    text = record(type(exc), exc, exc.__traceback__, context)
    if show:
        _show(text, context)
    return text


def install_error_reporting(root=None):
    """Route interpreter and Tk callback exceptions to the log plus a dialog."""
    def _hook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        _show(record(exc_type, exc_value, exc_tb, "beklenmeyen"), "beklenmeyen")

    sys.excepthook = _hook

    if root is not None:
        def _tk_hook(exc_type, exc_value, exc_tb):
            _show(record(exc_type, exc_value, exc_tb, "arayüz"), "arayüz")
        try:
            root.report_callback_exception = _tk_hook
            # Toplevels resolve the hook through their own class, so patch the
            # class as well: every widget in this interpreter then reports.
            import tkinter as tk
            tk.Tk.report_callback_exception = staticmethod(_tk_hook)
            tk.Toplevel.report_callback_exception = staticmethod(_tk_hook)
        except Exception:
            pass


def guarded_build(window, build, context: str):
    """Run a window's build step; on failure destroy the window and report.

    Without this a half-built Toplevel stays on screen: empty, and often
    unclosable because the WM_DELETE_WINDOW protocol had not been set yet.
    """
    try:
        build()
        return True
    except Exception as exc:
        report(exc, context)
        try:
            window.destroy()
        except Exception:
            pass
        return False
