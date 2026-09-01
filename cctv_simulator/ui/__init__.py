"""CCTV Dual View Simulator UI Subpackage.

Lazy on purpose. Eagerly importing every window here meant that a missing or
broken optional dependency - customtkinter for the optics workbench, numpy for
the 3D terrain window - stopped the entire application from starting, instead
of stopping the one window that needs it.

`from cctv_simulator.ui import CameraDatabaseWindow` still works; it just
imports `camera_db_window` and nothing else.
"""

_EXPORTS = {
    "DualViewCCTVDesignApp": "main_window",
    "TerrainViewshedWindow": "map_3d_window",
    "OpticsWorkbenchWindow": "modern_window",
    "Camera3DViewWindow": "view_3d_window",
    "CameraDatabaseWindow": "camera_db_window",
    "SpecAssistantWindow": "spec_assistant",
}

__all__ = list(_EXPORTS)


def __getattr__(name):
    """PEP 562 lazy attribute access."""
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib
    obj = getattr(importlib.import_module(f".{module_name}", __name__), name)
    globals()[name] = obj
    return obj


def __dir__():
    return sorted(set(globals()) | set(__all__))
