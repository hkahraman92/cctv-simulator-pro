"""
ttkbootstrap theme initialization helper.
Provides a graceful fallback to standard tk.Tk if ttkbootstrap is not installed.

ttkbootstrap's own widget classes (e.g. ttkbootstrap.Button) accept `bootstyle`.
Standard tkinter.ttk widgets get the global theme styling automatically but
don't support per-widget bootstyle.  We provide a `StyledButton` factory that
uses ttkbootstrap.Button when available and falls back to ttk.Button otherwise.
"""
import tkinter as tk
from tkinter import ttk as _ttk
from .config import configure_tk_paths

configure_tk_paths()

_TTKBOOTSTRAP_AVAILABLE = False
_ttkbootstrap_mod = None
try:
    import ttkbootstrap as _tbs
    _TTKBOOTSTRAP_AVAILABLE = True
    _ttkbootstrap_mod = _tbs
except ImportError:
    pass


LIGHT_THEME = "cosmo"

# Colors matched to cosmo light theme
COLORS = {
    "canvas_bg": "#F8F9FA",
    "text_bg": "#FFFFFF",
    "text_fg": "#212529",
    "accent": "#2780E3",
    "success": "#3FB618",
    "warning": "#FF7518",
    "danger": "#FF0039",
    "info": "#9954BB",
    "header_fg": "#0D6EFD",
    "muted_fg": "#6C757D",
    "treeview_uyumlu": "#D1E7DD",
    "treeview_kismi": "#FFF3CD",
    "treeview_uyumsuz": "#F8D7DA",
    "treeview_bulunamadi": "#E9ECEF",
}


# Try setting DPI awareness for Windows so high DPI screens scale properly
try:
    import ctypes
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # Per-monitor DPI aware
except Exception:
    try:
        import ctypes
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def fit_and_center_window(window: tk.Tk, default_w: int = 1400, default_h: int = 860,
                           min_w: int = 850, min_h: int = 550, maximize: bool = True):
    """Dynamically sizes and centers a window based on the user's screen resolution.

    On Windows, if ``maximize`` is True the window starts maximized so the user
    gets maximum usable area regardless of monitor size.  A sensible restored-
    size is still set so that un-maximizing produces a good result.
    """
    screen_w = window.winfo_screenwidth()
    screen_h = window.winfo_screenheight()

    # Restored-size: 93 % of screen, capped at the requested defaults
    target_w = min(default_w, max(min_w, int(screen_w * 0.93)))
    target_h = min(default_h, max(min_h, int(screen_h * 0.88)))

    # Centered position
    pos_x = max(0, (screen_w - target_w) // 2)
    pos_y = max(0, (screen_h - target_h) // 2 - 20)

    # Apply the "restored" geometry first
    window.geometry(f"{target_w}x{target_h}+{pos_x}+{pos_y}")
    window.minsize(min(min_w, screen_w - 40), min(min_h, screen_h - 80))

    # Auto-maximize on Windows so the application feels spacious by default
    if maximize:
        try:
            window.state("zoomed")          # Windows maximized state
        except tk.TclError:
            pass


def set_window_icon(window: tk.Tk):
    """Sets the application window icon using the generated CCTV logo."""
    try:
        from .config import resource_path
        icon_path = resource_path("assets/cctv_logo_64.png")
        if icon_path.exists():
            window._app_logo_img = tk.PhotoImage(file=str(icon_path), master=window)
            window.iconphoto(True, window._app_logo_img)
    except Exception:
        pass


def disable_ttkbootstrap(reason: str = ""):
    """Stop using ttkbootstrap widgets for the rest of this process.

    ttkbootstrap keeps a process-wide Style singleton. Once its engine fails to
    initialise (a missing msgcat, a destroyed master), every later
    ttkbootstrap widget raises too, and each failure takes down whatever window
    was being built. Falling back once, globally, keeps the application usable
    in plain ttk instead of losing a screen per click.
    """
    global _TTKBOOTSTRAP_AVAILABLE
    if not _TTKBOOTSTRAP_AVAILABLE:
        return
    _TTKBOOTSTRAP_AVAILABLE = False
    try:
        from .errors import note
        note("ttkbootstrap devre disi birakildi.\nSebep: " + (reason or "bilinmeyen")
             + "\nBundan sonra tum butonlar duz ttk.Button olarak olusturulacak.",
             "tema")
    except Exception:
        pass


def _ensure_msgcat(widget) -> str:
    """Load Tcl's msgcat package explicitly.

    ttkbootstrap calls ::msgcat::mcmset directly and assumes Tk already pulled
    msgcat in. When Tk's own initialisation is incomplete - which happens in a
    frozen build where the Tcl module path is not what Tcl expects - that
    command does not exist and every themed widget raises. Requiring the
    package by name loads it from the bundled tcl8/ modules.
    """
    try:
        if widget.tk.call("eval", "info commands ::msgcat::mcmset"):
            return "ok"
    except Exception:
        pass

    import os
    import sys
    from pathlib import Path
    
    # Try finding and adding tm search paths to Tcl
    search_dirs = []
    tcl_lib = os.environ.get("TCL_LIBRARY")
    if tcl_lib:
        search_dirs.extend([
            os.path.join(tcl_lib, "tcl8"),
            os.path.join(os.path.dirname(tcl_lib), "tcl8"),
            os.path.join(os.path.dirname(tcl_lib), "tcl8", "8.5"),
            os.path.join(os.path.dirname(tcl_lib), "tcl8", "8.6"),
        ])
    exe_dir = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
    search_dirs.extend([
        os.path.join(exe_dir, "tcl8"),
        os.path.join(exe_dir, "_internal", "tcl8"),
        os.path.join(exe_dir, "_internal", "_tcl_data", "tcl8"),
        os.path.join(exe_dir, "_tcl_data", "tcl8"),
    ])

    for cand in search_dirs:
        if os.path.isdir(cand):
            try:
                widget.tk.call("eval", f'tcl::tm::path add "{cand.replace(os.sep, "/")}"')
            except Exception:
                pass

    try:
        widget.tk.call("package", "require", "msgcat")
        return "ok"
    except Exception as exc:
        return f"basarisiz: {exc}"


def describe_tcl(widget) -> str:
    """One block naming what Tcl actually resolved. Written to the log."""
    import os
    lines = ["Tcl/Tk ortam raporu", "-" * 60,
             f"TCL_LIBRARY env : {os.environ.get('TCL_LIBRARY')}",
             f"TK_LIBRARY  env : {os.environ.get('TK_LIBRARY')}"]
    for label, script in (("info library", "info library"),
                          ("tcl patchlevel", "info patchlevel"),
                          ("tk version", "set ::tk_version"),
                          ("module paths", "tcl::tm::path list"),
                          ("msgcat yuklu", "info commands ::msgcat::mcmset")):
        try:
            lines.append(f"{label:15s}: {widget.tk.call('eval', script)}")
        except Exception as exc:
            lines.append(f"{label:15s}: HATA {exc}")
    lines.append(f"msgcat require : {_ensure_msgcat(widget)}")
    try:
        lines.append(f"msgcat komutu  : {widget.tk.call('eval', 'info commands ::msgcat::mcmset')}")
    except Exception as exc:
        lines.append(f"msgcat komutu  : HATA {exc}")
    lines.append(f"ttkbootstrap   : {'aktif' if _TTKBOOTSTRAP_AVAILABLE else 'devre disi'}")
    return "\n".join(lines)


def create_app_window(title: str = "Gelişmiş CCTV Görüş Alanı ve Proje Simülatörü",
                      geometry: str = "1400x860") -> tk.Tk:
    """Create the main application window with ttkbootstrap theme or fallback."""
    root = None
    bootstrap_error = ""
    if _TTKBOOTSTRAP_AVAILABLE:
        try:
            root = _ttkbootstrap_mod.Window(
                title=title,
                themename=LIGHT_THEME,
                size=(1400, 860),
            )
        except Exception as exc:
            bootstrap_error = str(exc)
            root = None

    if root is None:
        if _TTKBOOTSTRAP_AVAILABLE:
            disable_ttkbootstrap(bootstrap_error or "Window olusturulamadi")
        root = tk.Tk()
        root.title(title)
        style = _ttk.Style()
        style.theme_use("clam")

    # Set default root window globally so tkinter.font and customtkinter can always resolve it
    tk._default_root = root

    _ensure_msgcat(root)
    try:
        from .errors import note
        note(describe_tcl(root), "baslangic")
    except Exception:
        pass

    # Hide window while setting up geometry and layout to prevent visual flash/flicker
    try:
        root.withdraw()
    except Exception:
        pass

    fit_and_center_window(root, default_w=1400, default_h=860, min_w=900, min_h=600, maximize=True)
    set_window_icon(root)
    return root


def is_themed() -> bool:
    """Return True if ttkbootstrap is active."""
    return _TTKBOOTSTRAP_AVAILABLE


def StyledButton(parent, bootstyle: str = "", **kwargs):
    """Themed button, with a permanent fallback.

    A single ttkbootstrap failure used to propagate out of whichever window was
    being built and destroy it. Now the first failure switches the whole
    process to plain ttk and the button is still returned.
    """
    if _TTKBOOTSTRAP_AVAILABLE and bootstyle:
        try:
            return _ttkbootstrap_mod.Button(parent, bootstyle=bootstyle, **kwargs)
        except Exception as exc:
            disable_ttkbootstrap(f"StyledButton: {exc}")
    return _ttk.Button(parent, **kwargs)
