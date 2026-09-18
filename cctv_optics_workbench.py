"""
Launcher for the modern optics workbench UI.

    python cctv_optics_workbench.py

Needs customtkinter (and Pillow for the alpha-blended DORI zones):

    pip install customtkinter pillow

The classic dual-view application is unchanged and still starts with
`python cctv_dual_view_simulator.py`.
"""
import sys


def main() -> int:
    try:
        from cctv_simulator.ui.modern_window import launch
    except ImportError as exc:
        missing = getattr(exc, "name", "") or str(exc)
        if "customtkinter" in missing:
            print("customtkinter kurulu değil.\n\n    pip install customtkinter pillow\n",
                  file=sys.stderr)
            return 1
        raise
    from cctv_simulator.errors import install_error_reporting
    # BUGFIX: install_error_reporting(root=None) only sets sys.excepthook --
    # it skips the tk.Tk/tk.Toplevel.report_callback_exception class patch,
    # so exceptions raised inside this workbench's own Tk callbacks (ctk.CTk
    # is a tk.Tk subclass) were never caught in a windowed/frozen build.
    # A throwaway root triggers that class-level patch before the real
    # ModernOpticsWorkbench window (created inside launch()) exists.
    import tkinter as tk
    _bootstrap_root = tk.Tk()
    _bootstrap_root.withdraw()
    install_error_reporting(_bootstrap_root)
    _bootstrap_root.destroy()
    initial_model = sys.argv[1] if len(sys.argv) > 1 else None
    launch(initial_model)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
