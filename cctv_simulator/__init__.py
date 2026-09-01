"""CCTV Dual View Simulator Package.

Deliberately empty of eager imports.

Importing the submodules here pulled numpy, PIL, tkinter, ttkbootstrap,
customtkinter and the whole tile/network stack on `import cctv_simulator`
(measured: 362 ms, 654 modules). With this file left as a docstring it is
0.1 ms and 35 modules, and a missing optional dependency no longer stops the
application from starting - it only stops the one window that needs it.

Import what you need directly:

    from cctv_simulator.calculations import calculate_for_camera
    from cctv_simulator.ui.main_window import DualViewCCTVDesignApp
"""

__all__ = [
    "calculations",
    "compliance",
    "config",
    "database",
    "errors",
    "exporters",
    "models",
    "online_map_loader",
    "perimeter_planner",
    "perspective_3d",
    "terrain_loader",
    "theme",
    "ui",
    "viewshed_3d",
]


def __getattr__(name):
    """PEP 562 lazy attribute access.

    Keeps `import cctv_simulator; cctv_simulator.calculations` working without
    paying for every submodule at startup.
    """
    if name in __all__:
        import importlib
        module = importlib.import_module(f".{name}", __name__)
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
