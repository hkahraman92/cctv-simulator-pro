"""Headless load/save of a .json project file.

The GUI (``ui/main_window.save_project`` / ``load_project``) owns the on-disk
schema; this module reads and writes the SAME shape without a Tk dependency so
the CLI (``python -m cctv_simulator``) and tests can use it.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from .models import DEFAULT_LEVELS, CameraConfig, PPMLevel, TargetPoint

SCHEMA_VERSION = "2.0"


@dataclass
class ProjectData:
    cameras: List[CameraConfig]
    target_point: TargetPoint = field(default_factory=TargetPoint)
    ppm_levels: List[PPMLevel] = field(default_factory=lambda: [PPMLevel(**asdict(x)) for x in DEFAULT_LEVELS])
    lens_mode: str = "min"
    plan_path: str = ""
    plan_width_m: float = 45.0
    design_distance: str = "20.0"
    design_level: str = ""
    project_name: str = ""
    # Optional terrain block for the headless viewshed (--viewshed). Cameras use
    # their own pos_x_m / pos_y_m / heading_deg / tilt_deg / pole_height_m.
    terrain_source: str = "procedural"        # procedural | geotiff | region
    terrain_preset: str = "ridge_and_valley"
    terrain_file: str = ""
    terrain_region_id: str = ""               # region_library id when source == "region"
    terrain_width_m: float = 2000.0
    terrain_grid: int = 200            # procedural grid resolution (cells per side)
    viewshed_range_m: float = 1500.0
    weather: str = ""
    # Explicit camera pins on the terrain frame for --viewshed. Each:
    # {"camera": <name|index>, "x_m", "y_m", "mast_m", "pan_deg", "tilt_deg", "range_m"}
    # pan_deg is a compass bearing (0 = north, 90 = east); tilt_deg < 0 = down.
    placements: List[Dict[str, Any]] = field(default_factory=list)
    # Optional PTZ tour for --ptz:
    # {"camera": <name|index>, "x_m", "y_m", "mast_m", "range_m", "slew_speed_deg_s",
    #  "presets": [{"name", "pan_deg", "tilt_deg", "lens_mode", "dwell_s"}, ...]}
    ptz: Dict[str, Any] = field(default_factory=dict)
    # GUI-only keys of the terrain block (fence_points, line_mode, mixed_ptz, ...):
    # not used headless, but kept verbatim so a load + save here (CLI, new
    # desktop shell) no longer drops the fence the map window saved.
    terrain_extra: Dict[str, Any] = field(default_factory=dict)


_TERRAIN_KEYS = ("source", "preset", "file", "region_id", "width_m", "grid", "viewshed_range_m",
                 "weather", "placements", "ptz")


def load_project(path: str | Path) -> ProjectData:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    cam_list = data.get("cameras", [])
    if not cam_list:
        raise ValueError("Proje dosyasında kamera bulunamadı.")

    cameras = [CameraConfig(**_prune(c, CameraConfig)) for c in cam_list]

    target_data = data.get("target_point") or {}
    target = TargetPoint(**_prune(target_data, TargetPoint)) if target_data else TargetPoint()

    levels_data = data.get("ppm_levels") or []
    levels = [PPMLevel(**_prune(x, PPMLevel)) for x in levels_data] if levels_data else \
        [PPMLevel(**asdict(x)) for x in DEFAULT_LEVELS]

    terr = data.get("terrain") or {}
    return ProjectData(
        cameras=cameras,
        target_point=target,
        ppm_levels=levels,
        lens_mode=str(data.get("lens_mode", "min")),
        plan_path=str(data.get("plan_path", "")),
        plan_width_m=float(data.get("plan_width_m", 45.0)),
        design_distance=str(data.get("design_distance", "20.0")),
        design_level=str(data.get("design_level", "")),
        project_name=str(data.get("project_name", "")),
        terrain_source=str(terr.get("source", "procedural")),
        terrain_preset=str(terr.get("preset", "ridge_and_valley")),
        terrain_file=str(terr.get("file", "")),
        terrain_region_id=str(terr.get("region_id", "") or ""),
        terrain_width_m=float(terr.get("width_m", 2000.0)),
        terrain_grid=int(terr.get("grid", 200)),
        viewshed_range_m=float(terr.get("viewshed_range_m", 1500.0)),
        weather=str(terr.get("weather", "")),
        placements=list(terr.get("placements", []) or []),
        ptz=dict(terr.get("ptz", {}) or {}),
        terrain_extra={k: v for k, v in terr.items() if k not in _TERRAIN_KEYS},
    )


def save_project(path: str | Path, project: ProjectData) -> None:
    data = {
        "version": SCHEMA_VERSION,
        "cameras": [asdict(c) for c in project.cameras],
        "plan_path": project.plan_path,
        "plan_width_m": project.plan_width_m,
        "target_point": asdict(project.target_point),
        "ppm_levels": [asdict(x) for x in project.ppm_levels],
        "lens_mode": project.lens_mode,
        "design_distance": project.design_distance,
        "design_level": project.design_level,
        "project_name": project.project_name,
        "terrain": {
            "source": project.terrain_source,
            "preset": project.terrain_preset,
            "file": project.terrain_file,
            "region_id": project.terrain_region_id,
            "width_m": project.terrain_width_m,
            "grid": project.terrain_grid,
            "viewshed_range_m": project.viewshed_range_m,
            "weather": project.weather,
            "placements": project.placements,
            "ptz": project.ptz,
            **{k: v for k, v in project.terrain_extra.items() if k not in _TERRAIN_KEYS},
        },
    }
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=4), encoding="utf-8")


def _prune(raw: dict, cls) -> dict:
    """Drop keys the dataclass does not accept (older/newer project files)."""
    allowed = set(cls.__dataclass_fields__)
    return {k: v for k, v in raw.items() if k in allowed}
