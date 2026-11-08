"""Hidden approach and warning time: how close can an intruder get unseen?

A coverage percentage does not answer the question a border post or a
critical facility actually asks: *can someone reach the fence without being
seen, and if not, how many seconds does the guard force get?* An intruder
does not walk a random line -- he follows the dead ground (a dry stream bed,
the back of a ridge, a row of buildings) as far as it goes and only then
breaks cover.

Model, on the terrain grid inside an analysis window (target +- ``depth_m``):

* ``detected``  -- cells the plan's fixed cameras see at >= Algılama (25 px/m),
  from the authoritative multi-camera engine (the caller passes its grid).
* ``passable``  -- every cell except the target itself and buildings
  (``KIND_BUILDING``; trees are passable -- and already block the view).
* start         -- passable cells at least ``depth_m`` of walking from the
  target, on the side the intruder comes from (outside the polygon for a
  closed fence / area; the watched side for a border line; both sides for an
  open fence or a road).
* covert region -- passable, undetected cells reachable from the start
  through undetected cells only (flood fill).
* breach        -- the covert region touches the target: it can be reached
  unseen (warning 0).
* otherwise the intruder is first seen on a detected cell next to the covert
  region (or on a detected start cell); the warning distance is that cell's
  walking distance to the target (8-neighbour geodesic around buildings),
  minimised over all such cells. Warning time = distance / speed.

Per target sector the same minimum is taken over the breakout cells whose
nearest target cell lies in that sector, so the weak stretch of the fence is
named, not just the worst number.

Geometric detection only: a cell in view counts as detected (no probability
of detection, no camouflage); fixed cameras only (a PTZ looks elsewhere most
of the time). No Tk; numpy only.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .terrain_loader import TerrainData

DEFAULT_SPEEDS: Dict[str, float] = {"Yürüyerek": 1.4, "Koşarak": 4.0, "Araçla (40 km/sa)": 11.1}
SECTOR_M = 50.0

_NEIGH = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


@dataclass
class SectorWarning:
    x0: float
    y0: float
    x1: float
    y1: float
    warning_m: float               # inf = no approach reaches this stretch; 0 = reachable unseen


@dataclass
class ApproachResult:
    breach: bool
    warning_m: float               # 0 on a breach; inf if the target cannot be reached at all
    warning_s: Dict[str, float]    # per speed name
    response_s: Optional[float]
    sufficient: Dict[str, bool]    # warning_s >= response_s, per speed (empty without response_s)
    breakout_xy: Optional[Tuple[float, float]]   # where the intruder is first seen
    dash_path: List[Tuple[float, float]]         # breakout -> target, shortest walk
    breach_points: List[Tuple[float, float]]     # covert cells touching the target (thinned)
    sectors: List[SectorWarning]
    covert_area_m2: float
    window: Tuple[slice, slice]    # analysis window in grid rows / cols
    layer: np.ndarray              # full grid: 0 none, 2 detected (passable), 3 covert
    depth_m: float
    notes: List[str] = field(default_factory=list)


def _nodes(terrain: TerrainData, win: Tuple[slice, slice]):
    cs = terrain.cell_size_m
    cols = terrain.origin_x + np.arange(win[1].start, win[1].stop) * cs
    rows = terrain.origin_y + np.arange(win[0].start, win[0].stop) * cs
    return np.meshgrid(cols, rows)


def _shift(a: np.ndarray, dr: int, dc: int, fill) -> np.ndarray:
    """out[r, c] = a[r + dr, c + dc] (fill outside)."""
    out = np.full_like(a, fill)
    R, C = a.shape
    rs, re = max(0, -dr), min(R, R - dr)
    cs, ce = max(0, -dc), min(C, C - dc)
    out[rs:re, cs:ce] = a[rs + dr:re + dr, cs + dc:ce + dc]
    return out


def _dilate(m: np.ndarray) -> np.ndarray:
    out = m.copy()
    for dr, dc in _NEIGH:
        out |= _shift(m, dr, dc, False)
    return out


def flood(seeds: np.ndarray, allowed: np.ndarray) -> np.ndarray:
    """Cells of ``allowed`` 8-connected to ``seeds & allowed``."""
    cur = seeds & allowed
    while True:
        nxt = _dilate(cur) & allowed
        if (nxt == cur).all():
            return cur
        cur = nxt


def geodesic(sources: np.ndarray, passable: np.ndarray, cell_m: float) -> Tuple[np.ndarray, np.ndarray]:
    """8-neighbour walking distance from ``sources`` through ``passable``
    cells, and the flat index of the source each cell is nearest to.
    Iterative relaxation (exact for the 8-neighbour graph once stable)."""
    R, C = sources.shape
    dist = np.where(sources, 0.0, np.inf)
    label = np.where(sources, np.arange(R * C).reshape(R, C), -1)
    open_ = passable | sources
    steps = [(dr, dc, cell_m * (math.sqrt(2.0) if dr and dc else 1.0)) for dr, dc in _NEIGH]
    while True:
        changed = False
        for dr, dc, cost in steps:
            cand = _shift(dist, dr, dc, np.inf) + cost
            better = open_ & (cand < dist - 1e-9)
            if better.any():
                dist[better] = cand[better]
                label[better] = _shift(label, dr, dc, -1)[better]
                changed = True
        if not changed:
            return dist, label


def _segments(points: Sequence[Tuple[float, float]], closed: bool):
    pts = [(float(x), float(y)) for x, y in points]
    if closed and len(pts) > 2 and pts[0] != pts[-1]:
        pts.append(pts[0])
    return pts


def _line_cells(mx, my, pts, half_width):
    near = np.zeros(mx.shape, bool)
    for (x0, y0), (x1, y1) in zip(pts[:-1], pts[1:]):
        dx, dy = x1 - x0, y1 - y0
        L2 = dx * dx + dy * dy
        u = np.clip(((mx - x0) * dx + (my - y0) * dy) / L2, 0.0, 1.0) if L2 > 1e-12 else np.zeros(mx.shape)
        near |= np.hypot(x0 + u * dx - mx, y0 + u * dy - my) <= half_width
    return near


def _side(mx, my, pts):
    """+1 right / -1 left of the polyline's drawing direction, by the nearest segment."""
    best = np.full(mx.shape, np.inf)
    side = np.zeros(mx.shape)
    for (x0, y0), (x1, y1) in zip(pts[:-1], pts[1:]):
        dx, dy = x1 - x0, y1 - y0
        L2 = dx * dx + dy * dy
        if L2 < 1e-12:
            continue
        u = np.clip(((mx - x0) * dx + (my - y0) * dy) / L2, 0.0, 1.0)
        d = np.hypot(x0 + u * dx - mx, y0 + u * dy - my)
        cross = dx * (my - y0) - dy * (mx - x0)          # > 0: left of the direction
        take = d < best
        best[take] = d[take]
        side[take] = np.where(cross[take] > 0, -1.0, 1.0)
    return side


def _chainage(pts, x, y) -> float:
    best, at, run = None, 0.0, 0.0
    for (x0, y0), (x1, y1) in zip(pts[:-1], pts[1:]):
        dx, dy = x1 - x0, y1 - y0
        seg = math.hypot(dx, dy)
        if seg < 1e-9:
            continue
        u = max(0.0, min(1.0, ((x - x0) * dx + (y - y0) * dy) / (seg * seg)))
        d = math.hypot(x0 + u * dx - x, y0 + u * dy - y)
        if best is None or d < best:
            best, at = d, run + u * seg
        run += seg
    return at


def analyse_approach(terrain: TerrainData, detected: np.ndarray, target_points: Sequence[Tuple[float, float]], *,
                     mode: str = "facility", closed: bool = True, watch_side: str = "right",
                     depth_m: float = 400.0, speeds: Optional[Dict[str, float]] = None,
                     response_s: Optional[float] = None, sector_m: float = SECTOR_M) -> ApproachResult:
    """Hidden approach / warning time against a fence line, border, road or area.

    ``mode``: "facility" | "border" | "highway" | "area" (the plan's line mode);
    ``detected``: full-grid bool, cells seen at >= Algılama by fixed cameras."""
    from .surface_obstacles import KIND_BUILDING

    speeds = dict(speeds or DEFAULT_SPEEDS)
    cs = terrain.cell_size_m
    polygon = mode == "area" or (mode == "facility" and closed)
    pts = _segments(target_points, polygon)
    if len(pts) < 2:
        raise ValueError("Hedef hat en az iki nokta olmalı.")

    # analysis window: target bbox +- depth, clipped to the grid
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    c0 = max(0, int(math.floor((min(xs) - depth_m - terrain.origin_x) / cs)))
    c1 = min(terrain.cols, int(math.ceil((max(xs) + depth_m - terrain.origin_x) / cs)) + 1)
    r0 = max(0, int(math.floor((min(ys) - depth_m - terrain.origin_y) / cs)))
    r1 = min(terrain.rows, int(math.ceil((max(ys) + depth_m - terrain.origin_y) / cs)) + 1)
    win = (slice(r0, r1), slice(c0, c1))
    mx, my = _nodes(terrain, win)

    line = _line_cells(mx, my, pts, 0.75 * cs)
    if polygon:
        from .area_planner import _inside
        target = line | _inside(mx, my, pts)
    else:
        target = line
    if not target.any():
        raise ValueError("Hedef hat analiz ızgarasının dışında.")
    kind = terrain.obstacle_kind[win] if terrain.obstacle_kind is not None else None
    building = (kind == KIND_BUILDING) if kind is not None else np.zeros(target.shape, bool)
    passable = ~target & ~building
    seen = detected[win].astype(bool)

    # walking distance to the target (sources = target cells next to open ground)
    frontier = target & _dilate(passable)
    dist, label = geodesic(frontier, passable, cs)

    # where intruders come from: at least depth_m of walking away, on the approach side.
    # (The window edge alone is not enough: where the terrain clips the window, its
    # edge can sit right beside the end of a line.) Tiny terrain: the window edge.
    approach_side = np.ones(target.shape, bool)
    if mode == "border":
        approach_side = _side(mx, my, pts) == (1.0 if watch_side == "right" else -1.0)
    start = passable & approach_side & (dist >= depth_m - 1e-6) & np.isfinite(dist)
    if not start.any():
        edge = np.zeros(target.shape, bool)
        edge[0, :] = edge[-1, :] = edge[:, 0] = edge[:, -1] = True
        start = edge & passable & approach_side
        notes_start = "Arazi bu derinliğe yetmiyor: yaklaşma pencere kenarından başlatıldı."
    else:
        notes_start = ""

    covert = flood(start & ~seen, passable & ~seen)
    touching = covert & _dilate(target)
    breach = bool(touching.any())

    cand = (passable & seen & _dilate(covert)) | (start & seen)
    cand &= np.isfinite(dist)
    notes: List[str] = [notes_start] if notes_start else []
    breakout = None
    dash: List[Tuple[float, float]] = []
    if breach:
        warning_m = 0.0
    elif cand.any():
        k = np.flatnonzero(cand.ravel())[np.argmin(dist.ravel()[cand.ravel()])]
        r, c = divmod(int(k), dist.shape[1])
        warning_m = float(dist[r, c])
        breakout = (float(mx[r, c]), float(my[r, c]))
        dash = [breakout]
        while dist[r, c] > 0:                    # steepest descent to the target
            best = None
            for dr, dc in _NEIGH:
                rr, cc = r + dr, c + dc
                if 0 <= rr < dist.shape[0] and 0 <= cc < dist.shape[1] and (best is None or dist[rr, cc] < dist[best]):
                    best = (rr, cc)
            if best is None or dist[best] >= dist[r, c]:
                break
            r, c = best
            dash.append((float(mx[r, c]), float(my[r, c])))
    else:
        warning_m = math.inf
        notes.append("Yaklaşma tarafından hedefe yürünebilen bir yol yok (binalar ya da pencere sınırı).")

    # per-sector minimum along the target line
    total = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts[:-1], pts[1:]))
    n_sec = max(1, int(math.ceil(total / sector_m)))
    sec_min = np.full(n_sec, np.inf)
    hits = np.flatnonzero((touching | cand).ravel())
    W = dist.shape[1]
    chain: Dict[int, float] = {}
    for k in hits:
        lab = int(label.ravel()[k])
        if lab < 0:
            continue
        if lab not in chain:
            lr, lc = divmod(lab, W)
            chain[lab] = _chainage(pts, float(mx[lr, lc]), float(my[lr, lc]))
        ch = chain[lab]
        i = min(int(ch // sector_m), n_sec - 1)
        w = 0.0 if touching.ravel()[k] else float(dist.ravel()[k])
        sec_min[i] = min(sec_min[i], w)
    from .perimeter_planner import point_along_polyline
    sectors = []
    for i in range(n_sec):
        a = point_along_polyline(pts, i * sector_m)
        b = point_along_polyline(pts, min((i + 1) * sector_m, total))
        sectors.append(SectorWarning(float(a[0]), float(a[1]), float(b[0]), float(b[1]), float(sec_min[i])))

    bp = []
    if breach:
        rr, cc = np.nonzero(touching)
        step = max(1, len(rr) // 40)
        bp = [(float(mx[r, c]), float(my[r, c])) for r, c in zip(rr[::step], cc[::step])]
        notes.append("Hedefe görünmeden ulaşılabiliyor: kırmızı alan kameraların görmediği yaklaşma yolu.")

    warning_s = {k: (warning_m / v if math.isfinite(warning_m) else math.inf) for k, v in speeds.items()}
    sufficient = {k: s >= response_s for k, s in warning_s.items()} if response_s is not None else {}

    layer = np.zeros(detected.shape, np.int8)
    sub = np.zeros(target.shape, np.int8)
    sub[passable & seen] = 2
    sub[covert] = 3
    layer[win] = sub
    return ApproachResult(
        breach=breach, warning_m=warning_m, warning_s=warning_s, response_s=response_s, sufficient=sufficient,
        breakout_xy=breakout, dash_path=dash, breach_points=bp, sectors=sectors,
        covert_area_m2=float(covert.sum()) * cs * cs, window=win, layer=layer, depth_m=depth_m, notes=notes)
