"""Status panel content for the classic window: KPI tiles + ranked alarms.

Tk-free. Reads the optics engine's results; derives nothing physical itself
(distances, dead zone, px/m all come from :class:`OpticResult` and
``target_analysis_for_result``). The UI only draws what this returns.

Every state is one of ``ok`` / ``warn`` / ``danger`` / ``info`` / ``neutral``
and is always shown with its symbol and a word, never colour alone.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from .calculations import mode_label, target_analysis_for_result
from .models import CameraConfig, OpticResult, PPMLevel, TargetPoint

SEVERITY_ORDER = {"danger": 0, "warn": 1, "info": 2}
SYMBOL = {"ok": "●", "warn": "▲", "danger": "■", "info": "◆", "neutral": "·"}
WORD = {"ok": "UYGUN", "warn": "UYARI", "danger": "KRİTİK", "info": "BİLGİ", "neutral": ""}

# Same thresholds as calculations.build_recommendations, so a panel alarm and
# the recommendation text below the table never disagree.
DEAD_ZONE_WARN_M = 4.0
MIN_LUX_WARN = 0.05


@dataclass
class Alarm:
    severity: str                 # danger | warn | info
    title: str
    detail: str = ""
    camera_index: Optional[int] = None


@dataclass
class Kpi:
    label: str
    value: str
    unit: str = ""
    note: str = ""
    state: str = "neutral"        # ok | warn | danger | info | neutral


def _fmt_m(value: float) -> str:
    if not math.isfinite(value):
        return "∞"
    return f"{value:.1f}" if value < 100 else f"{value:.0f}"


def camera_alarms(index: int, camera: CameraConfig, results: Sequence[OpticResult],
                  design_level: PPMLevel, n_cameras: int,
                  level_names: Optional[Dict[str, str]] = None) -> List[Alarm]:
    out: List[Alarm] = []
    level_names = level_names or {}
    name = camera.name
    for result in results:
        mode = mode_label(result.mode)
        reach = result.ground_distances.get(design_level.key, 0.0)
        if reach <= result.dead_zone_m or reach <= 0:
            out.append(Alarm("danger", f"{name}: {design_level.name} hiçbir mesafede yok",
                             f"{mode} {result.focal_mm:g} mm · menzil kör noktada kalıyor", index))
        if result.dead_zone_m > 0.01 and n_cameras > 1 and not result.dead_zone_covered_by:
            out.append(Alarm("danger", f"{name}: kör nokta kapatılmıyor",
                             f"{mode} · {_fmt_m(result.dead_zone_m)} m, "
                             f"{result.dead_zone_area_m2:.0f} m² başka kamerada yok", index))
        elif result.dead_zone_m > DEAD_ZONE_WARN_M and not result.dead_zone_covered_by:
            out.append(Alarm("warn", f"{name}: kör nokta {_fmt_m(result.dead_zone_m)} m",
                             f"{mode} · alçaltmak veya tilt artırmak yakın alanı iyileştirir", index))
        beyond = [(d, k) for k, d in result.ground_distances.items()
                  if math.isfinite(d) and camera.ir_range_m > 0 and d > camera.ir_range_m]
        if beyond:
            # Name the nearest level that already outruns the IR: "1034 m"
            # alone came from a thermal Johnson level and read as nonsense.
            dist, key = min(beyond)
            label = level_names.get(key, key)
            more = f" (+{len(beyond) - 1} seviye)" if len(beyond) > 1 else ""
            out.append(Alarm("warn", f"{name}: gece IR menzili yetersiz",
                             f"{mode} · IR {camera.ir_range_m:.0f} m; {label} → {_fmt_m(dist)} m{more}",
                             index))
        if result.top_ray_deg <= 0:
            out.append(Alarm("info", f"{name}: üst ışın ufkun üstünde",
                             f"{mode} · tilt artırılabilir veya dar açı seçilebilir", index))
    if camera.min_lux > MIN_LUX_WARN:
        out.append(Alarm("warn", f"{name}: düşük ışıkta zayıf",
                         f"minimum {camera.min_lux:g} lux · ek aydınlatma gerekebilir", index))
    # In compare mode both lenses can raise the same title; keep the first.
    seen, unique = set(), []
    for alarm in out:
        if alarm.title not in seen:
            seen.add(alarm.title)
            unique.append(alarm)
    return unique


def target_alarms(target: TargetPoint, results: Sequence[OpticResult], level: PPMLevel,
                  camera_index: int) -> List[Alarm]:
    if not target.active or not results:
        return []
    out = []
    for result in results:
        data = target_analysis_for_result(target, result, level)
        if data["status"] != "uygun":
            out.append(Alarm("danger", f"{target.name}: {level.name} sağlanmıyor",
                             f"{data['mode']} · {data['distance']:.1f} m, {data['ppm']:.0f} px/m · "
                             f"{data['status']}", camera_index))
            break
    return out


def build_alarms(cameras: Sequence[CameraConfig], all_results: Dict[str, List[OpticResult]],
                 design_level: PPMLevel, target: Optional[TargetPoint] = None,
                 selected_index: int = 0, error: str = "",
                 levels: Sequence[PPMLevel] = ()) -> List[Alarm]:
    """All open alarms, most severe first, then by camera order."""
    alarms: List[Alarm] = []
    names = {level.key: level.name for level in levels}
    if error:
        alarms.append(Alarm("danger", "Hesap yapılamadı", error))
    for index, camera in enumerate(cameras):
        alarms.extend(camera_alarms(index, camera, all_results.get(camera.name, []),
                                    design_level, len(cameras), names))
    if target is not None and 0 <= selected_index < len(cameras):
        selected = all_results.get(cameras[selected_index].name, [])
        alarms.extend(target_alarms(target, selected, design_level, selected_index))
    order = {id(a): i for i, a in enumerate(alarms)}
    alarms.sort(key=lambda a: (SEVERITY_ORDER.get(a.severity, 9),
                               -1 if a.camera_index is None else a.camera_index, order[id(a)]))
    return alarms


def build_kpis(results: Sequence[OpticResult], design_level: PPMLevel,
               target: Optional[TargetPoint], n_cameras: int) -> List[Kpi]:
    """Four tiles for the selected camera (first lens when comparing)."""
    if not results:
        return [Kpi("TASARIM MENZİLİ", "—"), Kpi("KÖR NOKTA", "—"),
                Kpi("GÖRÜŞ AÇISI", "—"), Kpi("KONTROL NOKTASI", "—")]
    r = results[0]
    reach = r.ground_distances.get(design_level.key, 0.0)
    reach_ok = reach > r.dead_zone_m and reach > 0
    tiles = [Kpi("TASARIM MENZİLİ", _fmt_m(reach) if reach_ok else "0", "m",
                 design_level.name if "px/m" in design_level.name
                 else f"{design_level.name} · {design_level.ppm:g} px/m",
                 "ok" if reach_ok else "danger")]

    if r.dead_zone_m < 0.01:
        dz_state = "ok"
    elif n_cameras > 1 and not r.dead_zone_covered_by:
        dz_state = "danger"
    elif r.dead_zone_covered_by:
        dz_state = "ok"
    elif r.dead_zone_m > DEAD_ZONE_WARN_M:
        dz_state = "warn"
    else:
        dz_state = "neutral"
    dz_note = (f"{r.dead_zone_area_m2:.0f} m² · kaplayan: {', '.join(r.dead_zone_covered_by)}"
               if r.dead_zone_covered_by else f"{r.dead_zone_area_m2:.0f} m²")
    tiles.append(Kpi("KÖR NOKTA", _fmt_m(r.dead_zone_m), "m", dz_note, dz_state))

    tiles.append(Kpi("GÖRÜŞ AÇISI", f"{r.hfov_deg:.1f}", "°",
                     f"dikey {r.vfov_deg:.1f}° · {r.focal_mm:g} mm", "neutral"))

    if target is None or not target.active:
        tiles.append(Kpi("KONTROL NOKTASI", "—", "", "seçilmedi", "neutral"))
    else:
        data = target_analysis_for_result(target, r, design_level)
        ok = data["status"] == "uygun"
        tiles.append(Kpi("KONTROL NOKTASI", f"{data['ppm']:.0f}", "px/m",
                         f"{data['distance']:.1f} m · {data['status']}", "ok" if ok else "danger"))
    return tiles
