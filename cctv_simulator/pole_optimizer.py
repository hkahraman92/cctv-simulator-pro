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

import numpy as np

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
    best_spacing_m: Optional[float] = None    # exact spacing that built best_plan (trials carry it rounded)


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
    side = kw.get("watch_side") if kw.get("line_mode") == "border" else None
    roi = fence_roi(terrain, list(fence_points), closed, band_m, inside_m, side)
    pts = list(fence_points) + ([fence_points[0]] if closed and len(fence_points) > 2 else [])
    fence_len = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts[:-1], pts[1:]))

    _reach, _dz, s0 = calculate_optimal_spacing(
        camera, target_ppm=kw.get("target_ppm", 40.0), mast_height_m=kw.get("mast_height_m", 5.0),
        overlap_pct=kw.get("overlap_pct", 0.15), lens_mode=kw.get("lens_mode", "min"),
        visibility_km=vis, weather=weather)
    s_max = max(s0, fence_len / 2.0)

    cache: Dict[int, Tuple[PoleTrial, PerimeterPlanResult, LayoutResult]] = {}
    spacing_of: Dict[int, float] = {}
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
        spacing_of[n] = spacing
        trials.append(t)
        if progress:
            progress(len(trials), max_trials)
        return cache[n]

    base, _, _ = run(s0)
    lo_pass: Optional[float] = s0 if base.meets else None
    hi_fail: Optional[float] = None if base.meets else s0
    s = s0
    steps = 0
    last_fail: Optional[PoleTrial] = None if base.meets else base
    stalled: Optional[Tuple[PoleTrial, PoleTrial]] = None
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
            if lo_pass is None and last_fail is not None and t.fixed_poles > last_fail.fixed_poles:
                # Nothing has passed yet and we are only adding poles. A target the lens / mast
                # cannot reach saturates (47 % seen at 208 -> 1220 poles, 90 s): stop when denser
                # poles add nothing, or add so little that the remaining trials cannot get there.
                gain = t.optimised_frac - last_fail.optimised_frac
                growth = t.optimised_frac / max(last_fail.optimised_frac, 1e-9)
                diminishing = growth < math.sqrt(t.fixed_poles / last_fail.fixed_poles)
                left = max_trials - len(trials)
                if gain < 0.01 or (diminishing and t.optimised_frac + gain * left < goal_frac):
                    stalled = (last_fail, t)
                    break
            if lo_pass is None:
                last_fail = t

    passing = [cache[k] for k in cache if cache[k][0].meets]
    notes = []
    if passing:
        t, plan, res = min(passing, key=lambda x: (x[0].fixed_poles, -x[0].optimised_frac))
        met = True
    else:
        t, plan, res = max(cache.values(), key=lambda x: x[0].optimised_frac)
        met = False
        if stalled is not None:
            a, b = stalled
            notes.append(f"Sıklaştırma kapsamayı artırmıyor: {a.fixed_poles} → {b.fixed_poles} direkte "
                         f"%{a.optimised_frac * 100:.1f} → %{b.optimised_frac * 100:.1f}; hedef "
                         f"%{goal_frac * 100:.0f} direk ekleyerek tutmaz. Arama durduruldu.")
        notes.append(f"Hedef %{goal_frac * 100:.0f} hiçbir aralıkta tutmadı; en iyi deneme "
                     f"%{t.optimised_frac * 100:.1f}. Daha uzun odaklı / yüksek çözünürlüklü kamera, "
                     f"daha yüksek direk ya da daha düşük hedef deneyin.")
    if ptz_camera is not None:
        notes.append("PTZ'ler korunur; talep üzerine erişimdir, sürekli kapsama hedefine sayılmaz.")
    trials.sort(key=lambda x: x.spacing_m)
    return PoleSearchResult(trials=trials, best=t, best_plan=plan, best_layout=res, base=base,
                            goal_frac=goal_frac, level=level, roi_area_m2=res.roi_area_m2, met=met,
                            notes=notes, best_spacing_m=spacing_of.get(t.fixed_poles))


# ── PTZ-assisted target ─────────────────────────────────────────────────────
@dataclass
class AssistTrial:
    ptz_every_n: int
    ptz_count: int
    assisted_share: float        # authoritative: detected by a fixed camera AND a PTZ holds the target in time
    fixed_poles: int = 0


@dataclass
class AssistedResult:
    plan: Optional[PerimeterPlanResult]   # fixed poles (optimised lenses) + co-mounted PTZs; best effort when not met
    met: bool
    pole_count: int
    fixed_cameras: int
    ptz_count: int
    ptz_every_n: int
    detect_share: float                   # (a) fixed cameras hold Detection continuously, share of the band
    assisted_share: float                 # (b) detected AND a PTZ holds the target within max_s
    max_s: float
    target_ppm: float                     # px/m the PTZ must hold (zone_ppm of the level)
    goal_frac: float
    level: int
    roi_area_m2: float
    trials: List[AssistTrial] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    capped: bool = False                  # the evaluation cap ended the PTZ search before it converged
    pole_search: Optional[PoleSearchResult] = None


def minimize_poles_assisted(terrain: TerrainData, fence_points: Sequence[Tuple[float, float]],
                            camera: CameraConfig, ptz_camera: CameraConfig, *, level: int, goal_frac: float = 0.95,
                            max_s: float = 10.0, plan_kwargs: Optional[Dict] = None, band_m: float = 40.0,
                            inside_m: Optional[float] = None, n_focals: int = 6, max_trials: int = 9,
                            position_steps: int = 0, max_ptz_evals: int = 14, max_densify: int = 2,
                            speed_deg_s: float = 20.0, end_dwell_s: float = 2.0,
                            progress: Optional[Callable[[int, int], None]] = None) -> AssistedResult:
    """Fewest fixed poles + fewest PTZs for a PTZ-ASSISTED target at ``level``.

    The requirement is the plan statement's (``plan_statement.plan_conformity``,
    ``assist``): over the protected band (a) the fixed cameras hold Detection
    continuously on >= ``goal_frac``, and (b) on >= ``goal_frac`` a cell is
    detected by a fixed camera AND some PTZ holds ``zone_ppm(level)`` there
    within ``max_s`` seconds of the alarm. ``level`` must be above Detection.

    Search: (1) ``minimize_poles`` at Detection gives the fixed line (its focal /
    tilt choices applied); (2) on that line the largest ``ptz_every_n`` for which
    (b) holds is bisected (n = 1 is a PTZ on every pole, n = the number of poles
    is a single PTZ; the fixed poles never move with n), each candidate scored by
    ``evaluate_mixed_perimeter`` + the masks the statement uses, and the chosen
    n and n + 1 are both evaluated; (3) if even a PTZ on every pole fails, the
    fixed line is densified (at most ``max_densify`` steps) before giving up.
    The reported shares are the final authoritative evaluation, never an
    estimate. The fixed-only search at ``level`` is deliberately not run."""
    from dataclasses import replace

    from .focal_optimizer import apply_layout, band_area
    from .network_plan import storage_tb
    from .perimeter_planner import _bitrate_mbps
    from .perimeter_ptz import evaluate_mixed_perimeter
    from .viewshed_3d import ZONE_IDENT, is_thermal_camera, zone_ppm

    if ptz_camera is None:
        raise ValueError("PTZ destekli hedef için bir PTZ modeli seçin.")
    if level <= ZONE_DETECT:
        raise ValueError("PTZ destekli hedef için seviye Algılama'nın üstünde olmalı: Gözlem, Tanıma ya da Teşhis. "
                         "Algılama hedefi için sabit kamera aramasını (En az direk) kullanın.")
    target_ppm = zone_ppm(level, is_thermal_camera(ptz_camera))
    if (level == ZONE_IDENT and is_thermal_camera(camera)) or not math.isfinite(target_ppm):
        raise ValueError("Termal kamerada Teşhis (yüz, 250 px/m) seviyesi yoktur; Johnson algılama, tanıma ya da "
                         "teşhis seviyesini seçin.")
    if not max_s > 0:
        raise ValueError("PTZ varış süresi sıfırdan büyük olmalı.")
    kw = dict(plan_kwargs or {})
    if kw.get("line_mode") == "area":
        raise ValueError("PTZ destekli en az direk araması çit / sınır / otoyol içindir; alan modunda yoktur.")
    span = float(kw.get("ptz_patrol_span_deg", 360.0))
    for k in ("ptz_camera", "ptz_every_n", "ptz_patrol_span_deg"):
        kw.pop(k, None)                       # the fixed line is searched without PTZs
    closed = bool(kw.get("is_closed_loop", True))
    vis = float(kw.get("visibility_km", 40.0))
    weather = str(kw.get("weather", ""))
    side = kw.get("watch_side") if kw.get("line_mode") == "border" else None
    fence = list(fence_points)
    roi = fence_roi(terrain, fence, closed, band_m, inside_m, side)
    n_roi = int(roi.sum())
    goal = goal_frac - 1e-9

    search = minimize_poles(terrain, fence, camera, plan_kwargs=kw, goal_frac=goal_frac, level=ZONE_DETECT,
                            band_m=band_m, inside_m=inside_m, n_focals=n_focals, max_trials=max_trials,
                            position_steps=position_steps)
    plan_ppm = float(kw.get("target_ppm", 40.0))

    def finish_fixed(plan, layout):
        return apply_layout(plan, layout, camera, plan_ppm, terrain=terrain, visibility_km=vis, weather=weather)

    def with_ptz(fixed: PerimeterPlanResult, spacing: float, n: int) -> PerimeterPlanResult:
        host = generate_perimeter_plan(terrain, fence, camera, spacing_m=spacing, ptz_camera=ptz_camera,
                                       ptz_every_n=n, ptz_patrol_span_deg=span, **kw)
        bw = round(fixed.estimated_bandwidth_mbps + len(host.ptz_cameras) * _bitrate_mbps(ptz_camera), 1)
        return replace(fixed, ptz_cameras=host.ptz_cameras,
                       camera_count=len(fixed.placed_cameras) + len(host.ptz_cameras),
                       estimated_bandwidth_mbps=bw, estimated_storage_30days_tb=round(storage_tb(bw), 1))

    evals = [0]
    trials: List[AssistTrial] = []

    def assisted(plan: PerimeterPlanResult, det: np.ndarray, n: int) -> float:
        mixed = evaluate_mixed_perimeter(terrain, plan, camera, ptz_camera, speed_deg_s=speed_deg_s,
                                         end_dwell_s=end_dwell_s, cue_target_ppm=target_ppm,
                                         visibility_km=vis, weather=weather, fixed_reach_level=ZONE_DETECT)
        share = float((det & (mixed.detect_to_identify_s <= max_s)).sum()) / n_roi if n_roi else 0.0
        evals[0] += 1
        trials.append(AssistTrial(ptz_every_n=n, ptz_count=len(plan.ptz_cameras), assisted_share=share,
                                  fixed_poles=plan.pole_count))
        if progress:
            progress(evals[0], max_ptz_evals)
        return share

    capped = False
    bisected = False
    found = None
    attempts = []             # failed attempts: (plan, n, detect_share, assisted_share)
    # fixed lines to try: the Detection search's winner, then (only if it met) a few denser ones
    spacings: List[Optional[float]] = [search.best_spacing_m]
    if search.met and search.best_spacing_m:
        spacings += [search.best_spacing_m * 0.8 ** k for k in range(1, max_densify + 1)]
    prev_poles = -1
    for i, sp in enumerate(spacings):
        if i == 0:
            fixed = finish_fixed(search.best_plan, search.best_layout)
        else:
            plan0 = generate_perimeter_plan(terrain, fence, camera, spacing_m=sp, **kw)
            if plan0.pole_count <= prev_poles:
                continue
            lay = optimize_layout(terrain, mounts_from_plan(plan0, camera), roi=roi, level=ZONE_DETECT,
                                  objective="area", n_focals=n_focals, visibility_km=vis, weather=weather)
            fixed = finish_fixed(plan0, lay)
        prev_poles = fixed.pole_count
        mv, _a = band_area(terrain, fixed, camera, roi, ZONE_DETECT, visibility_km=vis, weather=weather)
        det = (mv.dori_grid >= ZONE_DETECT) & roi
        det_share = float(det.sum()) / n_roi if n_roi else 0.0
        if det_share < goal:                              # (a) fails: PTZs cannot repair it
            attempts.append((fixed, 1, det_share, 0.0))
            continue
        plans: Dict[int, Tuple[PerimeterPlanResult, float]] = {}

        def score(n: int):
            if n not in plans:
                p = with_ptz(fixed, sp, n)
                plans[n] = (p, assisted(p, det, n))
            return plans[n]

        # PTZ density: n = 1 (every pole) ... n = number of poles (a single PTZ)
        full_plan, full_share = score(1)
        if full_share < goal:                             # (b) fails even with a PTZ on every pole
            attempts.append((full_plan, 1, det_share, full_share))
            continue
        regular = len(full_plan.ptz_cameras)
        n_pass, n_fail = 1, None
        if regular > 1:
            if score(regular)[1] >= goal:
                n_pass = regular
            else:
                n_fail = regular
        while n_fail is not None and n_fail - n_pass > 1:
            if evals[0] >= max_ptz_evals:
                capped = True
                break
            mid = (n_pass + n_fail) // 2
            if score(mid)[1] >= goal:
                n_pass = mid
            else:
                n_fail = mid
        found = (plans[n_pass][0], n_pass, det_share, plans[n_pass][1])
        bisected = n_fail is not None
        break

    notes: List[str] = []
    if found is not None:
        plan, n, det_share, share = found
        met = True
        if plan.pole_count > search.best.fixed_poles:
            notes.append(f"Sabit hat {search.best.fixed_poles} → {plan.pole_count} direğe sıklaştırıldı: "
                         f"PTZ'ler tek başına yetmedi.")
        if bisected:
            notes.append(f"PTZ sayısı ({plan.ptz_count}) ikiye bölme aramasıyla bulunan en küçük sayıdır; daha seyrek "
                         f"bir dizilimin olmadığı tüm seçenekler denenerek kanıtlanmadı.")
        if capped:
            notes.append(f"PTZ araması {max_ptz_evals} değerlendirme sınırında durdu; her {n}. direkte bir PTZ hedefi "
                         f"tutuyor ama en az PTZ olmayabilir.")
    else:
        met = False
        plan, n, det_share, share = max(attempts, key=lambda a: (a[2] >= goal, a[3], a[2]))
        if det_share < goal:
            notes.append(f"(a) Sabit kameralar Algılamayı sürekli tutmuyor: %{det_share * 100:.1f} < "
                         f"%{goal_frac * 100:.0f}. Daha geniş görüşlü / yüksek çözünürlüklü kamera ya da daha "
                         f"yüksek direk deneyin.")
        else:
            notes.append(f"(b) Her direğe PTZ konsa bile alarmdan {max_s:g} sn içinde {target_ppm:g} px/m tutulan "
                         f"pay %{share * 100:.1f} < %{goal_frac * 100:.0f} (sürekli algılama %{det_share * 100:.1f} "
                         f"tamam). Daha uzun süre, daha hızlı / uzun odaklı PTZ ya da daha düşük seviye deneyin.")
        notes.append("Hedef tutmadı; plan değiştirilmedi.")
    return AssistedResult(
        plan=plan, met=met, pole_count=plan.pole_count, fixed_cameras=len(plan.placed_cameras),
        ptz_count=len(plan.ptz_cameras), ptz_every_n=n, detect_share=det_share, assisted_share=share, max_s=max_s,
        target_ppm=target_ppm, goal_frac=goal_frac, level=level, roi_area_m2=float(n_roi) * terrain.cell_size_m ** 2,
        trials=sorted(trials, key=lambda t: (t.fixed_poles, t.ptz_every_n)), notes=notes, capped=capped,
        pole_search=search)
