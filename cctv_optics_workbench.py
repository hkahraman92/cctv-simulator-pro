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
    install_error_reporting()
    initial_model = sys.argv[1] if len(sys.argv) > 1 else None
    launch(initial_model)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
