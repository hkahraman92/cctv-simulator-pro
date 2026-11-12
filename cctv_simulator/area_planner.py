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
6. Holes: the goal ratio leaves (1 - goal) of the polygon uncovered, and the
   greedy leaves it scattered. With ``max_hole_m2`` the uncovered cells are
   grouped into holes (8-connected) and every hole larger than that gets
   views aimed at it until none is left or no candidate reaches it. The
   report always lists the holes (count, largest, total) from the engine.
7. PTZ (optional): a PTZ is co-mounted on every Nth pole, patrolling around
   the polygon's centre. It is not counted in the continuous share (a PTZ
   looks away most of the time); coverage reports it as intermittent.

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
from .focal_optimizer import (
    LEVEL_NAME,
    LEVELS,
    _cone,
    _crop,
    _mount_range,
    auto_tilts,
    focal_steps,
    level_ppm,
)
from .models import CameraConfig
from .mount_visibility import mount_visibility, view_layers
from .perimeter_planner import PerimeterPlanResult, PlacedCamera, _bitrate_mbps
from .network_plan import storage_tb as _storage_tb
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
    holes_count: int = 0           # engine: uncovered patches inside the target
    largest_hole_m2: float = 0.0
    uncovered_m2: float = 0.0
    hole_cameras: int = 0          # cameras the hole pass added


def hole_areas(mask: np.ndarray, cell_area: float) -> Tuple[np.ndarray, List[float]]:
    """8-connected patches of ``mask``: (label grid, 0 = none; area per label
    1..n in m2). Min-label propagation in numpy (no scipy in the core)."""
    if not mask.any():
        return np.zeros(mask.shape, np.int64), []
    h, w = mask.shape
    big = np.iinfo(np.int64).max
    lab = np.where(mask, np.arange(1, mask.size + 1, dtype=np.int64).reshape(mask.shape), big)
    while True:
        p = np.pad(lab, 1, constant_values=big)
        nb = lab.copy()
        for dy in (0, 1, 2):
            for dx in (0, 1, 2):
                nb = np.minimum(nb, p[dy:dy + h, dx:dx + w])
        nb = np.where(mask, nb, big)
        if np.array_equal(nb, lab):
            break
        lab = nb
    _, inv, counts = np.unique(lab[mask], return_inverse=True, return_counts=True)
    out = np.zeros(mask.shape, np.int64)
    out[mask] = inv + 1
    return out, [float(c) * cell_area for c in counts]


def polygon_mask(terrain: TerrainData, polygon: Sequence[Tuple[float, float]]) -> np.ndarray:
    """Grid nodes inside ``polygon`` (even-odd rule), same node frame as
    ``fence_roi``: node (r, c) at origin + (c, r) * cell."""
    cols = terrain.origin_x + np.arange(terrain.cols) * terrain.cell_size_m
    rows = terrain.origin_y + np.arange(terrain.rows) * terrain.cell_size_m
    mx, my = np.meshgrid(cols, rows)
    return _inside(mx, my, polygon)


def target_mask(terrain: TerrainData, polygon: Sequence[Tuple[float, float]]) -> np.ndarray:
    """What an area plan must see: the polygon WITHOUT building footprints --
    seeing a roof is not seeing a person (with the surface model on, roofs
    used to count as covered area). Trees stay: a person under a canopy is a
    real blind spot, not something to leave out."""
    from . import surface_obstacles as obs
    roi = polygon_mask(terrain, polygon)
    kind = getattr(terrain, "obstacle_kind", None)
    if kind is not None:
        roi &= kind != obs.KIND_BUILDING
    return roi


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
              max_hole_m2: Optional[float] = None, min_cameras: int = 1,
              ptz_camera: Optional[CameraConfig] = None, ptz_every_n: int = 0, ptz_span_deg: float = 360.0,
              progress: Optional[Callable[[int, int], None]] = None) -> AreaPlanReport:
    """Fewest cameras (and poles) putting ``goal_frac`` of the polygon at >= ``level``.

    ``roi_mask`` narrows the target to part of the polygon (e.g. its streets);
    ``sites_fn(step_m)`` replaces the boundary / grid pole sites (e.g. points
    along the streets, where lamp posts stand)."""
    from . import surface_obstacles as obs

    ring = _ring(polygon)
    if len(ring) < 4:
        raise ValueError("Alan için en az üç köşe gerekir.")
    roi = target_mask(terrain, ring)
    if roi_mask is not None:
        roi &= roi_mask
    n_roi = int(roi.sum())
    if n_roi == 0:
        if roi_mask is None and polygon_mask(terrain, ring).any():
            raise ValueError("Çizilen alanın tamamı bina ayak izi: çatı hedef sayılmaz (insan orada durmaz). "
                             "Alanı binanın çevresine çizin ya da 'Binalar görüşü engellesin'i kapatın.")
        raise ValueError("Çizilen alan arazi ızgarasının hiçbir hücresini içermiyor."
                         if roi_mask is None else "Çizilen alanda hedef hücre (ör. sokak) yok.")
    cell_area = terrain.cell_size_m ** 2
    ppm = level_ppm(camera, level)
    if not math.isfinite(ppm):
        raise ValueError("Termal kamerada Teşhis (yüz) seviyesi yoktur; hedef olarak Johnson algılama, "
                         "tanıma ya da teşhis seviyesini seçin.")
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

    focus: List[Optional[np.ndarray]] = [None]      # hole pass: only these cells count

    k = max(1, int(min_cameras))          # redundancy: a cell counts when k cameras hold the level

    def gain(i: int) -> float:
        v = views[i]
        if not v.zone.size:
            return 0.0
        if focus[0] is not None:          # cells the focus says are open, whatever the estimate thinks
            return float(((v.zone >= level) & focus[0][v.sl]).sum())
        return float(sum(w * ((cnt[lv][v.sl] < (k if lv == level else 1)) & (v.zone >= lv)).sum()
                         for lv, w in weights.items() if w))

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
        return float((cnt[level] >= k).sum()) / n_roi

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

    def close_holes(limit_m2: float, open_cells: np.ndarray) -> int:
        """Aim views at every hole larger than ``limit_m2``; returns cameras added.

        ``open_cells``: uncovered as far as we know -- the estimate's holes
        plus the engine's (the two sample rays differently, so the engine
        can leave a hole the estimate thinks is closed)."""
        added = 0
        open_cells = open_cells.copy()
        while len(chosen) < max_cameras:
            lab, areas = hole_areas(roi & open_cells, cell_area)
            big = [k + 1 for k, a in enumerate(areas) if a > limit_m2]
            if not big:
                break
            focus[0] = np.isin(lab, big)
            best, best_v = -1, 0.0
            for i in range(len(views)):
                if i in chosen_set:
                    continue
                v = value(i)
                if v > best_v:
                    best, best_v = i, v
            focus[0] = None
            if best < 0 or best_v < 2.0:           # no candidate reaches what is left
                break
            s = views[best].site
            chosen.append(best)
            chosen_set.add(best)
            per_site[s] = per_site.get(s, 0) + 1
            add(best, +1)
            v = views[best]
            open_cells[v.sl] &= ~((v.zone >= level) & (cnt[level][v.sl] >= k))
            steps.append(AreaStep(len(chosen), len(per_site), frac()))
            added += 1
        return added

    def held_by(mv_) -> np.ndarray:
        """Cells k physical cameras hold at the level (engine, per camera)."""
        if k == 1:
            return mv_.dori_grid >= level
        n = np.zeros(mv_.dori_grid.shape, np.int32)
        for r in mv_.per_camera:
            n += (r.dori_grid >= level)
        return n >= k

    def engine(plan_):
        from .focal_optimizer import band_area
        from .viewshed_3d import ZONE_DETECT
        # the same engine run (every pole out to its Algılama reach) as coverage, approach and
        # city analysis: a cut at the target level's reach samples rays differently and gave
        # the same plan a second, slightly different share
        mv_, _ = band_area(terrain, plan_, camera, roi, ZONE_DETECT, visibility_km=visibility_km, weather=weather)
        return mv_

    chosen_set: set = set()
    target = goal_frac
    engine_frac = 0.0
    plan = None
    mv = None
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
        mv = engine(plan)
        a = float((held_by(mv) & roi).sum()) * cell_area
        engine_frac = a / (n_roi * cell_area)
        if engine_frac >= goal_frac or frac() < target - 1e-9 or target >= 1.0:
            break                                  # met, or the greedy has nothing more to give
        target = min(1.0, target + (goal_frac - engine_frac) + 0.01)

    hole_cams = 0
    if max_hole_m2 is not None and plan is not None and plan.placed_cameras:
        limit = max(float(max_hole_m2), cell_area)
        for _ in range(MAX_ROUNDS):
            open_cells = cnt[level] < k
            if mv is not None:
                open_cells |= roi & ~held_by(mv)
            got = close_holes(limit, open_cells)
            if not got:
                break
            hole_cams += got
            plan = _build_plan(terrain, ring, camera, [views[i] for i in chosen], site_xy, mast_height_m, ppm,
                               frac() * 100.0)
            if not verify:
                engine_frac = frac()
                break
            mv = engine(plan)
            engine_frac = float((held_by(mv) & roi).sum()) / n_roi
    plan.coverage_percentage = round(engine_frac * 100.0, 1)
    uncovered = roi & ~held_by(mv) if mv is not None else roi & (cnt[level] < k)
    _, holes = hole_areas(uncovered, cell_area)
    if ptz_camera is not None and ptz_every_n >= 1 and plan.placed_cameras:
        plan = _with_ptz(plan, ring, ptz_camera, ptz_every_n, ptz_span_deg, visibility_km, weather)

    met = engine_frac >= goal_frac
    if not met:
        why = (f"kamera tavanına ({max_cameras}) ulaşıldı" if len(chosen) >= max_cameras
               else "kalan hücreleri hiçbir aday görmüyor")
        best = f"{engine_frac * 100:.1f}".replace(".", ",")
        notes.append(f"Hedef %{goal_frac * 100:.0f} tutmadı ({why}); en iyi %{best}. "
                     f"Daha yüksek direk, daha uzun odak ya da alan içine direk deneyin.")
    if k > 1:
        notes.append(f"Yedeklilik: her hücre en az {k} kamerayla sayılır (aynı direkteki iki kamera da iki sayılır).")
    if hole_cams:
        notes.append(f"Delik kapatma: {hole_cams} kamera eklendi (sınır {max_hole_m2:.0f} m²).")
    if max_hole_m2 is not None and holes and max(holes) > max_hole_m2:
        notes.append(f"{sum(a > max_hole_m2 for a in holes)} delik {max_hole_m2:.0f} m² sınırından büyük kaldı: "
                     "aday direklerden oraya yeterli görüş yok (engel / menzil / kamera tavanı).")
    if skipped:
        notes.append(f"{skipped} aday direk yeri bina / ağaç içinde kaldığı için atlandı.")
    notes.append(f"Aday direk yerleri {step:.0f} m aralıkla: {len(site_xy)} yer"
                 + (" (sokak üzerinde)." if sites_fn is not None
                    else " (sınır + alan içi)." if interior else " (yalnız sınır)."))
    return AreaPlanReport(plan=plan, level=level, goal_frac=goal_frac, area_m2=n_roi * cell_area,
                          estimate_frac=frac(), engine_frac=engine_frac, met=met,
                          sites_considered=len(site_xy), sites_skipped_obstacle=skipped, steps=steps, notes=notes,
                          holes_count=len(holes), largest_hole_m2=max(holes, default=0.0),
                          uncovered_m2=float(sum(holes)), hole_cameras=hole_cams)


def _with_ptz(plan: PerimeterPlanResult, ring, ptz: CameraConfig, every_n: int, span_deg: float,
              visibility_km: float, weather: str) -> PerimeterPlanResult:
    """Co-mount a PTZ on every Nth pole, patrolling around the polygon's centre."""
    from dataclasses import replace

    from .perimeter_planner import _place_ptz_cameras
    from .viewshed_3d import identify_ppm

    hosts, seen = [], set()
    for c in plan.placed_cameras:
        if c.pole_id not in seen:
            seen.add(c.pole_id)
            hosts.append(c)
    hosts = hosts[::int(every_n)]
    pts = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else ring
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    centres = [math.degrees(math.atan2(cx - h.x_m, cy - h.y_m)) % 360.0 for h in hosts]
    ptz_cams = _place_ptz_cameras(list(ring), [(h, 0.0) for h in hosts], ptz, line_mode="area", is_closed_loop=True,
                                  span_deg=span_deg, target_ppm=identify_ppm(ptz), visibility_km=visibility_km,
                                  weather=weather, centres=centres)
    mbps = plan.estimated_bandwidth_mbps + len(ptz_cams) * _bitrate_mbps(ptz)
    return replace(plan, ptz_cameras=ptz_cams, camera_count=len(plan.placed_cameras) + len(ptz_cams),
                   estimated_bandwidth_mbps=round(mbps, 1),
                   estimated_storage_30days_tb=round(_storage_tb(mbps), 1))


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
        estimated_storage_30days_tb=round(_storage_tb(mbps), 1), line_mode="area")


__all__ = ["AreaPlanReport", "AreaStep", "candidate_sites", "hole_areas", "plan_area", "polygon_mask", "target_mask",
           "LEVEL_NAME"]
