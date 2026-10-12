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


# Widgets that scroll themselves; the wheel over them must not also move the
# surrounding scroll container.
_SELF_SCROLLING = {"Treeview", "Text", "Listbox", "TCombobox", "Canvas"}


def _route_mousewheel(event):
    w = event.widget
    if not isinstance(w, tk.Misc) or w.winfo_class() in _SELF_SCROLLING:
        return
    while w is not None:
        if getattr(w, "_cctv_scroller", False):
            top, bottom = w.yview()
            if top > 0.0 or bottom < 1.0:
                w.yview_scroll(int(-event.delta / 120) or (-1 if event.delta > 0 else 1), "units")
            return
        w = w.master


def make_scrollable(parent, padding=8):
    """Vertical scroll container for a form/panel that can outgrow its window.

    Returns ``(outer, inner)``: add ``outer`` to the parent layout (e.g. a
    notebook tab), build the content into ``inner``. The scrollbar only shows
    when the content is taller than the viewport, and ``inner`` is stretched
    to the viewport width so ``sticky="ew"`` / ``fill=X`` children still span
    it. The mouse wheel is routed by the widget under the pointer, not by
    ``<Enter>``/``<Leave>`` on the canvas -- Tk sends ``<Leave>`` to the canvas
    as soon as the pointer moves onto any field inside it, so the old
    bind_all/unbind_all pattern dropped the wheel over the actual content.
    """
    outer = _ttk.Frame(parent)
    canvas = tk.Canvas(outer, highlightthickness=0, borderwidth=0)
    canvas._cctv_scroller = True
    try:
        bg = _ttk.Style(outer).lookup("TFrame", "background")
        if bg:
            canvas.configure(background=bg)
    except tk.TclError:
        pass
    sb = _ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=sb.set)
    canvas.grid(row=0, column=0, sticky="nsew")
    outer.rowconfigure(0, weight=1)
    outer.columnconfigure(0, weight=1)
    inner = _ttk.Frame(canvas, padding=padding)
    win = canvas.create_window((0, 0), window=inner, anchor="nw")

    def _sync(_e=None):
        # A Canvas requests a fixed default width (~10 cm), not its content's:
        # without this the surrounding layout (e.g. a notebook in a sash pane)
        # sizes to that default and squeezes/truncates wider content.
        req_w = inner.winfo_reqwidth()
        if int(canvas.cget("width")) != req_w:
            canvas.configure(width=req_w)
        canvas.configure(scrollregion=canvas.bbox("all"))
        if inner.winfo_reqheight() > canvas.winfo_height() > 1:
            sb.grid(row=0, column=1, sticky="ns")
        else:
            sb.grid_remove()
            canvas.yview_moveto(0.0)

    inner.bind("<Configure>", _sync)
    canvas.bind("<Configure>", lambda e: (canvas.itemconfigure(win, width=e.width), _sync()))
    outer.after_idle(_sync)
    # bind_all is interpreter-wide (every window shares it): register once,
    # detected from the Tcl binding itself so a second window or a second Tk
    # root in the same process never adds a duplicate (double-speed) handler.
    if "_route_mousewheel" not in outer.bind_all("<MouseWheel>"):
        outer.bind_all("<MouseWheel>", _route_mousewheel, add="+")
    return outer, inner


def flow_layout(container, widgets, *, padx=3, pady=2, justify="left", expand=False):
    """Lay ``widgets`` (children of ``container``) out left-to-right, wrapping
    onto a new row instead of squeezing when the container is too narrow.

    With plain pack/grid, a row of fixed-size buttons in a narrow window gives
    the last ones a sliver or zero width: their text is cut, or they vanish
    entirely (no error, just gone). The container must be stretched by its
    parent (``fill=X`` / ``sticky="ew"``) -- its width drives the wrapping.
    ``justify="right"`` right-aligns each row; ``expand=True`` stretches the
    widgets in a row to share its full width. An item may be a tuple/list of
    widgets kept together on one row (e.g. a caption and its combobox).
    """
    rows = []
    state = {"key": None}
    themed = container.winfo_class() != "Frame"
    items = [tuple(i) if isinstance(i, (list, tuple)) else (i,) for i in widgets]
    widgets = [w for item in items for w in item]

    def _row(i):
        while len(rows) <= i:
            rows.append(_ttk.Frame(container) if themed
                        else tk.Frame(container, bg=container.cget("bg")))
        return rows[i]

    def _relayout(_e=None):
        width = container.winfo_width()
        groups, cur, x = [], [], 0
        for item in items:
            ww = sum(w.winfo_reqwidth() + 2 * padx for w in item)
            if cur and width > 1 and x + ww > width:
                groups.append(cur)
                cur, x = [], 0
            cur.extend(item)
            x += ww
        if cur:
            groups.append(cur)
        key = [[str(w) for w in g] for g in groups]
        if key == state["key"]:
            return
        state["key"] = key
        for w in widgets:
            w.pack_forget()
        for f in rows:
            f.pack_forget()
        for i, group in enumerate(groups):
            row = _row(i)
            row.pack(fill=tk.X)
            for w in (reversed(group) if justify == "right" else group):
                if expand:
                    w.pack(in_=row, side=tk.LEFT, fill=tk.X, expand=True, padx=padx, pady=pady)
                elif justify == "right":
                    w.pack(in_=row, side=tk.RIGHT, padx=padx, pady=pady)
                else:
                    w.pack(in_=row, side=tk.LEFT, padx=padx, pady=pady)
                # The row frames are created after the widgets, so they sit
                # above them in the stacking order and would hide them.
                w.lift(row)

    container.bind("<Configure>", _relayout, add="+")
    for w in widgets:
        # a label whose text changes changes its requested width
        w.bind("<Configure>", _relayout, add="+")
    _relayout()
    return _relayout


def wrap_label(label, pad=4, initial=280):
    """Make a (ttk or tk) Label wrap to its current width instead of being cut
    off. The label must be stretched by its parent (``fill=X`` /
    ``sticky="ew"``). ``initial`` keeps a long text from requesting its whole
    one-line width before the first layout pass (which would force its parent
    wider than the window)."""
    label.configure(wraplength=initial)

    def _fit(e):
        wl = max(40, e.width - pad)
        if int(str(label.cget("wraplength")) or 0) != wl:
            label.configure(wraplength=wl)

    label.bind("<Configure>", _fit, add="+")
    return label


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


def create_app_window(title: str = "",
                      geometry: str = "1400x860") -> tk.Tk:
    """Create the main application window with ttkbootstrap theme or fallback."""
    if not title:
        from .i18n import t as _t
        title = _t("Gelişmiş CCTV Görüş Alanı ve Proje Simülatörü")
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
