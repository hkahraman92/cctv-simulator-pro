"""Fewest poles that still meet a fence-coverage goal.

The perimeter planner spaces poles for the lens END it is given (the wide end
by default) and a target px/m, with a safety overlap -- a conservative,
per-pole rule that knows nothing about what the neighbours cover. With the
focal / tilt of every pole free (focal_optimizer), a sparser layout often
still covers the band. This searches the pole spacing:

1. the planner's own spacing is trial 0 (each pole's focal / tilt then
   optimised on the union coverage of the fence band);
2. if it meets the goal, the spacing grows geometrically until a trial fails,
   then is bisected between the last pass and the first fail;
   if it does not, the spacing shrinks (denser) until it passes;
3. the passing trial with the fewest fixed poles wins (ties: more coverage).

Coverage = area of the fence band (``fence_roi``, +-band_m) at >= ``level``,
continuous (fixed cameras only), as the focal optimiser scores it; the UI
re-scores the winner with the authoritative multi-camera engine. A PTZ plan
(``ptz_camera`` + ``ptz_every_n`` in ``plan_kwargs``) keeps its PTZs: they are
on-demand reach, reported, never counted toward the continuous goal.

No Tk.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .focal_optimizer import LayoutResult, fence_roi, mounts_from_plan, optimize_layout
from .models import CameraConfig
from .perimeter_planner import PerimeterPlanResult, calculate_optimal_spacing, generate_perimeter_plan
from .terrain_loader import TerrainData
from .viewshed_3d import ZONE_DETECT


@dataclass
class PoleTrial:
    spacing_m: float
    fixed_poles: int
    total_cameras: int
    planner_frac: float          # band coverage with the planner's own focal / tilt
    optimised_frac: float        # after per-pole focal / tilt optimisation
    meets: bool


@dataclass
class PoleSearchResult:
    trials: List[PoleTrial]
    best: Optional[PoleTrial]
    best_plan: Optional[PerimeterPlanResult]
    best_layout: Optional[LayoutResult]
    base: PoleTrial              # trial 0: the planner's spacing
    goal_frac: float
    level: int
    roi_area_m2: float
    met: bool
    notes: List[str] = field(default_factory=list)


def minimize_poles(terrain: TerrainData, fence_points: Sequence[Tuple[float, float]], camera: CameraConfig,
                   *, plan_kwargs: Optional[Dict] = None, goal_frac: float = 0.95, level: int = ZONE_DETECT,
                   objective: str = "area", band_m: float = 40.0, inside_m: Optional[float] = None,
                   n_focals: int = 6, max_trials: int = 9,
                   position_steps: int = 0,
                   progress: Optional[Callable[[int, int], None]] = None) -> PoleSearchResult:
    """Fewest fixed poles that hold ``goal_frac`` of the fence band at ``level``.

    ``position_steps`` > 0: every trial also lets each pole move
    ±k × spacing / (2·steps + 2) along the fence (terrain-aware placement:
    a pole behind a ridge or a building slides to where it sees the band)."""
    kw = dict(plan_kwargs or {})
    closed = bool(kw.get("is_closed_loop", True))
    vis = float(kw.get("visibility_km", 40.0))
    weather = str(kw.get("weather", ""))
    ptz_camera = kw.get("ptz_camera") if kw.get("ptz_every_n") else None
    roi = fence_roi(terrain, list(fence_points), closed, band_m, inside_m)
    pts = list(fence_points) + ([fence_points[0]] if closed and len(fence_points) > 2 else [])
    fence_len = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts[:-1], pts[1:]))

    _reach, _dz, s0 = calculate_optimal_spacing(
        camera, target_ppm=kw.get("target_ppm", 40.0), mast_height_m=kw.get("mast_height_m", 5.0),
        overlap_pct=kw.get("overlap_pct", 0.15), lens_mode=kw.get("lens_mode", "min"),
        visibility_km=vis, weather=weather)
    s_max = max(s0, fence_len / 2.0)

    cache: Dict[int, Tuple[PoleTrial, PerimeterPlanResult, LayoutResult]] = {}
    trials: List[PoleTrial] = []

    def run(spacing: float):
        plan = generate_perimeter_plan(terrain, list(fence_points), camera, spacing_m=spacing, **kw)
        n = plan.pole_count                          # corner guards share their vertex pole
        if n in cache:                                # same pole count -> same layout question
            return cache[n]
        step = spacing / (2 * position_steps + 2) if position_steps else 0.0
        mounts = mounts_from_plan(plan, camera, ptz_camera, position_step_m=step, position_steps=position_steps)
        res = optimize_layout(terrain, mounts, roi=roi,
                              level=level, objective=objective, n_focals=n_focals,
                              visibility_km=vis, weather=weather)
        area = max(res.roi_area_m2, 1e-9)
        t = PoleTrial(spacing_m=round(spacing, 1), fixed_poles=n, total_cameras=plan.camera_count,
                      planner_frac=res.before_m2[level] / area, optimised_frac=res.after_m2[level] / area,
                      meets=res.after_m2[level] / area >= goal_frac)
        cache[n] = (t, plan, res)
        trials.append(t)
        if progress:
            progress(len(trials), max_trials)
        return cache[n]

    base, _, _ = run(s0)
    lo_pass: Optional[float] = s0 if base.meets else None
    hi_fail: Optional[float] = None if base.meets else s0
    s = s0
    steps = 0
    # trials only counts distinct pole counts; steps bounds the loop itself
    while len(trials) < max_trials and steps < 3 * max_trials:
        steps += 1
        if lo_pass is not None and hi_fail is not None:
            if hi_fail - lo_pass < max(2.0, 0.04 * lo_pass):
                break
            s = math.sqrt(lo_pass * hi_fail)         # bisect in log space
        elif lo_pass is not None:                    # still passing: go sparser
            if s >= s_max:
                break
            s = min(s * 1.45, s_max)
        else:                                        # still failing: go denser
            s = s * 0.7
            if s < 3.0:
                break
        t, _, _ = run(s)
        if t.meets:
            lo_pass = s if lo_pass is None else max(lo_pass, s)
        else:
            hi_fail = s if hi_fail is None else min(hi_fail, s)

    passing = [cache[k] for k in cache if cache[k][0].meets]
    notes = []
    if passing:
        t, plan, res = min(passing, key=lambda x: (x[0].fixed_poles, -x[0].optimised_frac))
        met = True
    else:
        t, plan, res = max(cache.values(), key=lambda x: x[0].optimised_frac)
        met = False
        notes.append(f"Hedef %{goal_frac * 100:.0f} hiçbir aralıkta tutmadı; en iyi deneme "
                     f"%{t.optimised_frac * 100:.1f}. Daha uzun odaklı / yüksek çözünürlüklü kamera, "
                     f"daha yüksek direk ya da daha düşük hedef deneyin.")
    if ptz_camera is not None:
        notes.append("PTZ'ler korunur; talep üzerine erişimdir, sürekli kapsama hedefine sayılmaz.")
    trials.sort(key=lambda x: x.spacing_m)
    return PoleSearchResult(trials=trials, best=t, best_plan=plan, best_layout=res, base=base,
                            goal_frac=goal_frac, level=level, roi_area_m2=res.roi_area_m2, met=met,
                            notes=notes)
