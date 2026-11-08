"""Area goal: fewest cameras that hold a DORI level over a drawn polygon.

The perimeter planner protects a LINE (the fence band, +-40 m). A car park,
a yard, a storage area or a square is an AREA: the question is "which poles,
which lenses, which headings put >= 95 % of this polygon at Recognition".

1. Candidate pole sites: along the polygon boundary and (optionally) on a
   grid inside it, spaced from the camera's reach at the target level and
   capped at ``max_sites``. A site whose cell holds a building / tree taller
   than the lens is skipped (the camera would sit inside it).
2. Candidate views per site, all on the cached 360-degree mount raster
   (``mount_visibility``) -- the same building blocks as the fixed-view
   engine (Kural 1): focal steps x auto tilts x pans, each cropped to the
   ROI cells it reaches.
3. Lazy greedy set cover: the view adding the most not-yet-covered ROI cells
   at >= the level is placed next. A view on a pole already standing is
   preferred unless a new pole's view is clearly better (``NEW_POLE_FACTOR``)
   -- a pole costs a foundation and a trench, a second camera on it does not.
   ``max_per_pole`` cameras per pole.
4. Redundant cameras are pruned (latest first) while the goal still holds.
5. The result goes through the AUTHORITATIVE multi-camera engine
   (``focal_optimizer.band_area``, every pole out to its Algılama reach --
   the same run coverage reports). If the engine scores below the goal the
   greedy continues with a raised internal goal (a few rounds); the reported
   fraction is always the engine's.

The plan is a ``PerimeterPlanResult`` (``line_mode="area"``, the polygon as
``fence_points``), so coverage, network / cost, night, scenarios and site
exports work on it unchanged. Several cameras may share a ``pole_id``.

No Tk.
"""
from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .calculations import camera_optics
from .focal_optimizer import LEVEL_NAME, LEVEL_PPM, LEVELS, _cone, _crop, _mount_range, auto_tilts, focal_steps
from .models import CameraConfig
from .mount_visibility import mount_visibility, view_layers
from .perimeter_planner import PerimeterPlanResult, PlacedCamera, _bitrate_mbps
from .terrain_loader import TerrainData
from .viewshed_3d import ZONE_RECOG

NEW_POLE_FACTOR = 0.8          # a new pole must add > 1/0.8 = 1.25x the cells of the best view on a standing one
MAX_ROUNDS = 3                 # engine-verification rounds


@dataclass
class AreaStep:
    cameras: int
    poles: int
    frac: float                    # estimate after this camera (mount rasters)


@dataclass
class AreaPlanReport:
    plan: PerimeterPlanResult
    level: int
    goal_frac: float
    area_m2: float
    estimate_frac: float           # mount-raster estimate of the final layout
    engine_frac: float             # authoritative multi-camera engine
    met: bool
    sites_considered: int
    sites_skipped_obstacle: int
    steps: List[AreaStep] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


def polygon_mask(terrain: TerrainData, polygon: Sequence[Tuple[float, float]]) -> np.ndarray:
    """Grid nodes inside ``polygon`` (even-odd rule), same node frame as
    ``fence_roi``: node (r, c) at origin + (c, r) * cell."""
    cols = terrain.origin_x + np.arange(terrain.cols) * terrain.cell_size_m
    rows = terrain.origin_y + np.arange(terrain.rows) * terrain.cell_size_m
    mx, my = np.meshgrid(cols, rows)
    return _inside(mx, my, polygon)


def _inside(x: np.ndarray, y: np.ndarray, polygon: Sequence[Tuple[float, float]]) -> np.ndarray:
    pts = [tuple(map(float, p)) for p in polygon]
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    inside = np.zeros(np.shape(x), bool)
    n = len(pts)
    for i in range(n):
        (x0, y0), (x1, y1) = pts[i], pts[(i + 1) % n]
        if y0 == y1:
            continue
        crosses = (y0 > y) != (y1 > y)
        xs = x0 + (y - y0) * (x1 - x0) / (y1 - y0)
        inside ^= crosses & (x < xs)
    return inside


def _ring(polygon: Sequence[Tuple[float, float]]) -> List[Tuple[float, float]]:
    pts = [(float(p[0]), float(p[1])) for p in polygon]
    if pts and pts[0] != pts[-1]:
        pts.append(pts[0])
    return pts


def _boundary_sites(ring: List[Tuple[float, float]], step: float) -> List[Tuple[float, float]]:
    out: List[Tuple[float, float]] = []
    for (x0, y0), (x1, y1) in zip(ring[:-1], ring[1:]):
        seg = math.hypot(x1 - x0, y1 - y0)
        if seg < 1e-6:
            continue
        n = max(1, int(math.ceil(seg / step)))
        out += [(x0 + (x1 - x0) * k / n, y0 + (y1 - y0) * k / n) for k in range(n)]
    return out


def _interior_sites(ring: List[Tuple[float, float]], step: float) -> List[Tuple[float, float]]:
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    gx = np.arange(min(xs) + step / 2, max(xs), step)
    gy = np.arange(min(ys) + step / 2, max(ys), step)
    if not len(gx) or not len(gy):
        return []
    mx, my = np.meshgrid(gx, gy)
    keep = _inside(mx, my, ring)
    return [(float(a), float(b)) for a, b in zip(mx[keep], my[keep])]


def candidate_sites(polygon: Sequence[Tuple[float, float]], spacing_m: float, *, interior: bool = True,
                    max_sites: int = 80) -> Tuple[List[Tuple[float, float]], float]:
    """Boundary (+ interior grid) sites; the spacing grows until at most
    ``max_sites`` remain. Returns (sites, spacing used)."""
    ring = _ring(polygon)
    step = max(float(spacing_m), 1.0)
    for _ in range(60):
        sites = _boundary_sites(ring, step) + (_interior_sites(ring, step) if interior else [])
        if len(sites) <= max_sites:
            return sites, step
        step *= 1.15
    return sites[:max_sites], step


def _ground_reach(camera: CameraConfig, focal: float, ppm: float, mast_m: float) -> float:
    slant = camera_optics(camera, focal).distance_for_ppm(ppm)
    return math.sqrt(max(slant * slant - mast_m * mast_m, 1.0))


@dataclass
class _View:
    site: int
    focal: float
    tilt: float
    pan: float
    hfov: float
    vfov: float
    sl: Tuple[slice, slice]
    zone: np.ndarray               # int8, ROI-masked, cropped


def _views_for_site(terrain, camera, x, y, mast, roi, level, n_focals, rng, vis, weather, site) -> List[_View]:
    mv = mount_visibility(terrain, x, y, mast, max_range_m=rng)
    roi_w = roi[mv.window]
    out: List[_View] = []
    if not roi_w.any():
        return out
    for f in focal_steps(camera, n_focals):
        for t in auto_tilts(camera, f, mast, level, rng):
            lay = view_layers(mv, camera, tilt_deg=t, focal_mm=f, max_range_m=rng, visibility_km=vis, weather=weather)
            zv = np.where(lay.visible & roi_w, lay.zone, 0)
            if not (zv >= level).any():
                continue
            step = max(10.0, min(30.0, lay.hfov_deg / 2.0))
            for p in np.arange(0.0, 360.0, step):
                z = np.where(_cone(lay.center_az_rad, float(p), lay.hfov_deg), zv, 0)
                if not (z >= level).any():
                    continue
                sl, zc = _crop(z, mv.window)
                out.append(_View(site, f, t, float(p), lay.hfov_deg, lay.vfov_deg, sl, zc))
    return out


def plan_area(terrain: TerrainData, polygon: Sequence[Tuple[float, float]], camera: CameraConfig, *,
              level: int = ZONE_RECOG, goal_frac: float = 0.95, mast_height_m: float = 6.0,
              interior: bool = True, max_per_pole: int = 2, max_cameras: int = 120, max_sites: int = 80,
              spacing_m: Optional[float] = None, n_focals: int = 5, visibility_km: float = 40.0,
              weather: str = "", verify: bool = True, roi_mask: Optional[np.ndarray] = None,
              sites_fn: Optional[Callable[[float], List[Tuple[float, float]]]] = None,
              progress: Optional[Callable[[int, int], None]] = None) -> AreaPlanReport:
    """Fewest cameras (and poles) putting ``goal_frac`` of the polygon at >= ``level``.

    ``roi_mask`` narrows the target to part of the polygon (e.g. its streets);
    ``sites_fn(step_m)`` replaces the boundary / grid pole sites (e.g. points
    along the streets, where lamp posts stand)."""
    from . import surface_obstacles as obs

    ring = _ring(polygon)
    if len(ring) < 4:
        raise ValueError("Alan için en az üç köşe gerekir.")
    roi = polygon_mask(terrain, ring)
    if roi_mask is not None:
        roi &= roi_mask
    n_roi = int(roi.sum())
    if n_roi == 0:
        raise ValueError("Çizilen alan arazi ızgarasının hiçbir hücresini içermiyor."
                         if roi_mask is None else "Çizilen alanda hedef hücre (ör. sokak) yok.")
    cell_area = terrain.cell_size_m ** 2
    ppm = LEVEL_PPM[level]
    tele = float(max(camera.focal_max_mm, camera.focal_min_mm))
    reach_tele = _ground_reach(camera, tele, ppm, mast_height_m)
    # open ground: half the tele reach; street poles: lamp-post density (buildings cut the sightlines)
    step0 = spacing_m or max(2.0 * terrain.cell_size_m, (0.25 if sites_fn is not None else 0.5) * reach_tele)
    if sites_fn is None:
        sites, step = candidate_sites(ring, step0, interior=interior, max_sites=max_sites)
    else:
        step = step0
        sites = sites_fn(step)
        while len(sites) > max_sites:
            step *= 1.15
            sites = sites_fn(step)
    xs, ys = [p[0] for p in ring], [p[1] for p in ring]
    cap = _mount_range(camera, None)

    views: List[_View] = []
    site_xy: List[Tuple[float, float]] = []
    skipped = 0
    for i, (x, y) in enumerate(sites):
        if obs.mount_obstruction_m(terrain, x, y, mast_height_m) > 0:
            skipped += 1
            continue
        far = max(math.hypot(px - x, py - y) for px, py in zip(xs, ys)) + 2.0 * terrain.cell_size_m
        rng = min(cap, far)
        k = len(site_xy)
        site_xy.append((x, y))
        views += _views_for_site(terrain, camera, x, y, mast_height_m, roi, level, n_focals, rng,
                                 visibility_km, weather, k)
        if progress:
            progress(i + 1, len(sites) + MAX_ROUNDS)

    cnt = {lv: np.zeros(roi.shape, np.int32) for lv in LEVELS}
    weights = {lv: (1.0 if lv == level else (1e-3 if lv > level else 0.0)) for lv in LEVELS}
    chosen: List[int] = []
    per_site: Dict[int, int] = {}
    steps: List[AreaStep] = []

    def gain(i: int) -> float:
        v = views[i]
        if not v.zone.size:
            return 0.0
        return float(sum(w * ((cnt[lv][v.sl] == 0) & (v.zone >= lv)).sum() for lv, w in weights.items() if w))

    def value(i: int) -> float:
        s = views[i].site
        used = per_site.get(s, 0)
        if used >= max_per_pole:
            return 0.0
        g = gain(i)
        return g if (used or not per_site) else g * NEW_POLE_FACTOR

    def add(i: int, sign: int) -> None:
        v = views[i]
        for lv in LEVELS:
            cnt[lv][v.sl] += sign * (v.zone >= lv)

    def frac() -> float:
        return float((cnt[level] > 0).sum()) / n_roi

    heap = [(-value(i), i) for i in range(len(views))]
    heapq.heapify(heap)

    def greedy(target: float) -> None:
        while heap and frac() < target and len(chosen) < max_cameras:
            negv, i = heapq.heappop(heap)
            if i in chosen_set:
                continue
            v = value(i)
            if v <= 0.5 * weights[level]:          # nothing left this view adds
                continue
            if heap and v < -heap[0][0] - 1e-9:    # stale: re-queue with the fresh value
                heapq.heappush(heap, (-v, i))
                continue
            s = views[i].site
            new_site = s not in per_site
            chosen.append(i)
            chosen_set.add(i)
            per_site[s] = per_site.get(s, 0) + 1
            add(i, +1)
            steps.append(AreaStep(len(chosen), len(per_site), frac()))
            if new_site:                           # its views just got cheaper: re-queue them
                for j, w in enumerate(views):
                    if w.site == s and j not in chosen_set:
                        heapq.heappush(heap, (-value(j), j))

    def prune(target: float) -> None:
        for i in reversed(list(chosen)):
            add(i, -1)
            if frac() >= target:
                chosen.remove(i)
                chosen_set.discard(i)
                s = views[i].site
                per_site[s] -= 1
                if not per_site[s]:
                    del per_site[s]
            else:
                add(i, +1)

    chosen_set: set = set()
    target = goal_frac
    engine_frac = 0.0
    plan = None
    notes: List[str] = []
    for rnd in range(MAX_ROUNDS):
        greedy(target)
        prune(target)
        plan = _build_plan(terrain, ring, camera, [views[i] for i in chosen], site_xy, mast_height_m, ppm,
                           frac() * 100.0)
        if progress:
            progress(len(sites) + rnd + 1, len(sites) + MAX_ROUNDS)
        if not verify or not plan.placed_cameras:
            engine_frac = frac()
            break
        from .focal_optimizer import band_area
        from .viewshed_3d import ZONE_DETECT
        # the same engine run (every pole out to its Algılama reach) as coverage, approach and
        # city analysis: a cut at the target level's reach samples rays differently and gave
        # the same plan a second, slightly different share
        mv, _ = band_area(terrain, plan, camera, roi, ZONE_DETECT, visibility_km=visibility_km, weather=weather)
        a = float(((mv.dori_grid >= level) & roi).sum()) * cell_area
        engine_frac = a / (n_roi * cell_area)
        if engine_frac >= goal_frac or frac() < target - 1e-9 or target >= 1.0:
            break                                  # met, or the greedy has nothing more to give
        target = min(1.0, target + (goal_frac - engine_frac) + 0.01)
    plan.coverage_percentage = round(engine_frac * 100.0, 1)

    met = engine_frac >= goal_frac
    if not met:
        why = (f"kamera tavanına ({max_cameras}) ulaşıldı" if len(chosen) >= max_cameras
               else "kalan hücreleri hiçbir aday görmüyor")
        best = f"{engine_frac * 100:.1f}".replace(".", ",")
        notes.append(f"Hedef %{goal_frac * 100:.0f} tutmadı ({why}); en iyi %{best}. "
                     f"Daha yüksek direk, daha uzun odak ya da alan içine direk deneyin.")
    if skipped:
        notes.append(f"{skipped} aday direk yeri bina / ağaç içinde kaldığı için atlandı.")
    notes.append(f"Aday direk yerleri {step:.0f} m aralıkla: {len(site_xy)} yer"
                 + (" (sokak üzerinde)." if sites_fn is not None
                    else " (sınır + alan içi)." if interior else " (yalnız sınır)."))
    return AreaPlanReport(plan=plan, level=level, goal_frac=goal_frac, area_m2=n_roi * cell_area,
                          estimate_frac=frac(), engine_frac=engine_frac, met=met,
                          sites_considered=len(site_xy), sites_skipped_obstacle=skipped, steps=steps, notes=notes)


def _build_plan(terrain, ring, camera, chosen: List[_View], site_xy, mast, ppm, cov_pct) -> PerimeterPlanResult:
    pole_of: Dict[int, int] = {}
    cams: List[PlacedCamera] = []
    for v in chosen:
        pid = pole_of.setdefault(v.site, len(pole_of) + 1)
        x, y = site_xy[v.site]
        down = -v.tilt + v.vfov / 2.0                    # bottom ray below the horizon
        dz = mast / math.tan(math.radians(down)) if down > 2.0 else mast * 1.2
        cams.append(PlacedCamera(
            pole_id=pid, x_m=round(x, 1), y_m=round(y, 1), ground_z_m=round(terrain.ground_elevation_at(x, y), 1),
            mast_height_m=mast, pan_deg=round(v.pan % 360.0, 1), tilt_deg=round(v.tilt, 1),
            focal_mm=round(v.focal, 2), hfov_deg=round(v.hfov, 1), vfov_deg=round(v.vfov, 1),
            effective_range_m=round(_ground_reach(camera, v.focal, ppm, mast), 1), dead_zone_m=round(dz, 1),
            camera_model=camera.name, sensor_name=camera.sensor_name, resolution_name=camera.resolution_name))
    cams.sort(key=lambda c: (c.pole_id, c.pan_deg))
    segs = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(ring[:-1], ring[1:])]
    area = abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring[:-1], ring[1:]))) / 2.0
    poles = len(pole_of)
    mbps = len(cams) * _bitrate_mbps(camera)
    return PerimeterPlanResult(
        fence_points=list(ring), total_fence_length_m=round(sum(segs), 1), segment_lengths_m=[round(s, 1) for s in segs],
        placed_cameras=cams, gaps=[], camera_count=len(cams), pole_count=poles, target_ppm=ppm,
        avg_spacing_m=round(math.sqrt(area / poles), 1) if poles else 0.0,
        coverage_percentage=round(cov_pct, 1), estimated_bandwidth_mbps=round(mbps, 1),
        estimated_storage_30days_tb=round(mbps * 324.0 * 1.15 / 1024.0, 1), line_mode="area")


__all__ = ["AreaPlanReport", "AreaStep", "candidate_sites", "plan_area", "polygon_mask", "LEVEL_NAME"]
