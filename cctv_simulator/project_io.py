"""Headless load/save of a .json project file.

The GUI (``ui/main_window.save_project`` / ``load_project``) owns the on-disk
schema; this module reads and writes the SAME shape without a Tk dependency so
the CLI (``python -m cctv_simulator``) and tests can use it.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List

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

    return ProjectData(
        cameras=cameras,
        target_point=target,
        ppm_levels=levels,
        lens_mode=str(data.get("lens_mode", "min")),
        plan_path=str(data.get("plan_path", "")),
        plan_width_m=float(data.get("plan_width_m", 45.0)),
        design_distance=str(data.get("design_distance", "20.0")),
        design_level=str(data.get("design_level", "")),
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
    }
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=4), encoding="utf-8")


def _prune(raw: dict, cls) -> dict:
    """Drop keys the dataclass does not accept (older/newer project files)."""
    allowed = set(cls.__dataclass_fields__)
    return {k: v for k, v in raw.items() if k in allowed}
