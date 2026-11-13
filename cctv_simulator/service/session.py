"""Per-process state the shell refers to by id: loaded terrains, last results.

Grids stay in the core; the shell only gets summaries and pictures. A terrain
carries both its bare-earth and its surface version (buildings / trees) so the
"obstacles block the view" switch is a lookup, not a recomputation.
"""
from __future__ import annotations

import itertools
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from .protocol import APP_ERROR, RpcError

_MAX_TERRAINS = 4          # each holds full grids + an orthophoto; keep memory bounded


@dataclass
class TerrainEntry:
    id: str
    bare: Any                          # TerrainData, bare earth
    photo: Any = None                  # PIL image (north-up) or None
    surface: Any = None                # TerrainData with obstacles, or None
    obstacle_layer: Any = None
    city: Any = None                   # city.CityLayer: streets / residential / places (OSM)
    label: str = ""
    source: str = ""                   # procedural | region | geotiff | online
    region_id: str = ""
    attribution: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)
    # DORI target: a person this tall (m) standing on the cell; 0 = the ground
    # point (API default, as the engine). The shell sets 1.8 (terrain.settings).
    target_height_m: float = 0.0
    # last analysis results, for overlays / exports / follow-up calls
    results: Dict[str, Any] = field(default_factory=dict)

    def terrain(self, use_obstacles: bool = True):
        return self.surface if (use_obstacles and self.surface is not None) else self.bare

    # Results computed FROM a plan. A new plan drops them: a scenario snapshot,
    # KMZ or offer package must never pair plan B with plan A's coverage / cost.
    PLAN_DERIVED = ("multi", "band_share", "network", "cost", "night", "mixed", "approach", "ptz_reach", "coverage_masks", "privacy")

    def set_target_height(self, h: float) -> bool:
        """A new target height re-scores everything: plan-derived results are
        dropped (the plan itself stays). Returns whether it changed."""
        h = max(0.0, float(h))
        if h == self.target_height_m:
            return False
        self.target_height_m = h
        for k in self.PLAN_DERIVED + ("single",):
            self.results.pop(k, None)
        return True

    def set_plan(self, value) -> None:
        for k in self.PLAN_DERIVED:
            self.results.pop(k, None)
        self.results["plan"] = value


class Session:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self._terrains: Dict[str, TerrainEntry] = {}

    def add(self, entry_kwargs: Dict[str, Any]) -> TerrainEntry:
        with self._lock:
            tid = f"t{next(self._ids)}"
            entry = TerrainEntry(id=tid, **entry_kwargs)
            self._terrains[tid] = entry
            while len(self._terrains) > _MAX_TERRAINS:          # drop the oldest
                self._terrains.pop(next(iter(self._terrains)))
            return entry

    def get(self, tid: Any) -> TerrainEntry:
        with self._lock:
            entry = self._terrains.get(tid) if isinstance(tid, str) else None
        if entry is None:
            raise RpcError(APP_ERROR, "Arazi oturumda yok (süresi dolmuş olabilir); yeniden yükleyin.",
                           {"terrain_id": tid})
        return entry

    def drop(self, tid: str) -> None:
        with self._lock:
            self._terrains.pop(tid, None)


SESSION = Session()


def reset() -> Optional[Session]:
    """Tests: a fresh session."""
    global SESSION
    SESSION = Session()
    return SESSION
