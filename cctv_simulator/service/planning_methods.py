"""API v1: network / cabling plan, cost estimate and camera model comparison for a perimeter plan."""
from __future__ import annotations

import dataclasses
import math
from typing import Any, Dict

from .params import _camera_from
from .protocol import APP_ERROR, INVALID_PARAMS, Context, RpcError
from .terrain_methods import _num, _plan_json, _terrain_of



def _levels():
    from ..viewshed_3d import ZONE_DETECT, ZONE_IDENT, ZONE_OBSERVE, ZONE_RECOG
    return {"detect": ZONE_DETECT, "observe": ZONE_OBSERVE, "recognize": ZONE_RECOG, "identify": ZONE_IDENT}


def _record(name: str):
    from ..database import load_camera_library
    rec = load_camera_library().get(name)
    return rec if isinstance(rec, dict) else {}


def _prices(raw: Any):
    from ..network_plan import PriceBook
    if raw is None:
        return PriceBook()
    if not isinstance(raw, dict):
        raise RpcError(INVALID_PARAMS, "'prices' bir nesne olmalı.")
    known = {f.name for f in dataclasses.fields(PriceBook)}
    vals = {}
    for k, v in raw.items():
        if k not in known:
            raise RpcError(INVALID_PARAMS, f"Bilinmeyen fiyat alanı: {k}")
        if k == "currency":
            vals[k] = str(v)
        elif isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0:
            vals[k] = float(v)
        else:
            raise RpcError(INVALID_PARAMS, f"'{k}' sıfır ya da pozitif sayı olmalı.")
    return PriceBook(**vals)


def _net_json(net) -> Dict[str, Any]:
    return {**{k: v for k, v in dataclasses.asdict(net).items() if k != "switches"},
            "switches": [dataclasses.asdict(s) for s in net.switches]}


def _cost_json(cost) -> Dict[str, Any]:
    return dataclasses.asdict(cost)


def network_plan(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Switches along the fence, copper / fibre runs, PoE budget, NVR bitrate and storage; cost if priced."""
    from ..network_plan import estimate_cost, plan_network
    from ..perimeter_planner import _bitrate_mbps

    entry, _t = _terrain_of(params)
    if "plan" not in entry.results:
        raise RpcError(APP_ERROR, "Önce bir çit planı oluşturun.")
    plan, cam, kw, _fence = entry.results["plan"]
    ptz_cam = kw.get("ptz_camera")
    net = plan_network(
        plan, camera_model=_record(cam.model_name), ptz_model=_record(ptz_cam.model_name) if ptz_cam else None,
        bitrate_fixed_mbps=_bitrate_mbps(cam), bitrate_ptz_mbps=_bitrate_mbps(ptz_cam) if ptz_cam else 4.0,
        max_copper_m=_num(params, "max_copper_m", 90.0, lo=10, hi=100),
        switch_ports=int(_num(params, "switch_ports", 8, lo=1, hi=48)),
        poe_budget_w=_num(params, "poe_budget_w", 120.0, lo=10, hi=2000),
        nvr_at_m=_num(params, "nvr_at_m", 0.0, lo=0),
        retention_days=int(_num(params, "retention_days", 30, lo=1, hi=3650)))
    cost = estimate_cost(plan, net, _prices(params.get("prices")))
    entry.results["network"] = net
    entry.results["cost"] = cost
    return {"network": _net_json(net), "cost": _cost_json(cost)}


def compare_models(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """For each candidate model: fewest poles holding the goal, its network and cost; ranked."""
    from ..model_selection import compare_models as run
    from .terrain_methods import _fence, _plan_kwargs

    entry, t = _terrain_of(params)
    names = params.get("models")
    if not isinstance(names, list) or not names or not all(isinstance(n, str) for n in names):
        raise RpcError(INVALID_PARAMS, "'models' kamera modeli adlarından oluşan bir liste olmalı.")
    if len(names) > 12:
        raise RpcError(INVALID_PARAMS, "En çok 12 model karşılaştırılabilir.")
    from ..database import load_camera_library
    from .methods import library_camera
    lib = load_camera_library()
    base = _camera_from(params.get("camera", {}))
    cands = []
    for n in names:
        if not isinstance(lib.get(n), dict) or not lib[n]:
            raise RpcError(APP_ERROR, f"Kütüphanede model yok: {n}")
        cam = _camera_from(library_camera({"name": n, "base": dataclasses.asdict(base)}, ctx)["camera"])
        cands.append((n, cam, lib[n]))
    level = _levels().get(params.get("level", "detect"))
    if level is None:
        raise RpcError(INVALID_PARAMS, "'level' detect, observe, recognize ya da identify olmalı.")
    prices = params.get("model_prices") or {}
    if not isinstance(prices, dict):
        raise RpcError(INVALID_PARAMS, "'model_prices' nesne olmalı.")
    opts = run(t, _fence(params), cands, plan_kwargs=_plan_kwargs(params),
               goal_frac=_num(params, "goal_frac", 0.95, lo=0.05, hi=1.0), level=level,
               band_m=_num(params, "band_m", 40.0, lo=5, hi=500),
               position_steps=int(_num(params, "position_steps", 0, lo=0, hi=4)),
               prices={k: float(v) for k, v in prices.items() if isinstance(v, (int, float))},
               price_book=_prices(params.get("prices")),
               progress=lambda d, n, name: ctx.progress(d, n, f"{name}" if name else "Sıralanıyor"))
    entry.results["model_options"] = opts
    return {"options": [{
        "model_name": o.model_name, "met": o.met, "poles": o.poles, "band_frac": o.band_frac,
        "seconds": o.seconds, "notes": o.notes,
        "switches": len(o.network.switches) if o.network else 0,
        "copper_m": o.network.copper_m if o.network else 0.0,
        "storage_tb": o.network.storage_tb if o.network else 0.0,
        "cost": _cost_json(o.cost) if o.cost else None,
    } for o in opts]}


def compare_use(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Make one compared option the current plan (its camera, poles and lenses)."""
    entry, _t = _terrain_of(params)
    opts = entry.results.get("model_options") or []
    name = params.get("model_name")
    o = next((x for x in opts if x.model_name == name), None)
    if o is None or o.plan is None:
        raise RpcError(APP_ERROR, "Bu model için karşılaştırma sonucu yok; karşılaştırmayı yeniden çalıştırın.")
    from .methods import library_camera
    from .terrain_methods import _fence, _plan_kwargs
    # the plan belongs to THAT model: rebuild its camera on the caller's mounting
    cam = _camera_from(library_camera({"name": name, "base": params.get("camera", {})}, ctx)["camera"])
    entry.set_plan((o.plan, cam, _plan_kwargs(params), _fence(params)))
    return {"plan": _plan_json(o.plan), "camera": dataclasses.asdict(cam)}


def _finite(v: float):
    return float(v) if math.isfinite(v) else None


def approach_analyse(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Hidden approach: how close an intruder gets unseen, and the warning time it leaves (fixed cameras)."""
    from .. import map_render
    from ..approach import DEFAULT_SPEEDS, analyse_approach
    from ..focal_optimizer import band_area
    from ..viewshed_3d import ZONE_DETECT
    from .terrain_methods import _DISPLAY_PX, _image, _overlay_size

    entry, t = _terrain_of(params)
    if "plan" not in entry.results:
        raise RpcError(APP_ERROR, "Önce bir çit / alan planı oluşturun.")
    plan, cam, kw, fence = entry.results["plan"]
    if not plan.placed_cameras:
        raise RpcError(APP_ERROR, "Planda sabit kamera yok.")
    speeds = params.get("speeds") or DEFAULT_SPEEDS
    if not isinstance(speeds, dict) or not all(
            isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 for v in speeds.values()):
        raise RpcError(INVALID_PARAMS, "'speeds' ad → pozitif m/sn eşlemesi olmalı.")
    response = params.get("response_s")
    if response is not None:
        response = _num(params, "response_s", lo=0, hi=3600)
    ctx.progress(0, 2, "Algılama haritası (otoriter motor)")
    # always re-run: a cached map may belong to the other obstacle setting (use_obstacles)
    mv, _ = band_area(t, plan, cam, None, ZONE_DETECT, visibility_km=kw["visibility_km"], weather=kw["weather"])
    ctx.progress(1, 2, "Yaklaşma yolları")
    try:
        res = analyse_approach(t, mv.dori_grid >= ZONE_DETECT, fence, mode=kw.get("line_mode", "facility"),
                               closed=bool(kw.get("is_closed_loop", True)), watch_side=kw.get("watch_side", "right"),
                               depth_m=_num(params, "depth_m", 400.0, lo=50, hi=5000),
                               speeds={str(k): float(v) for k, v in speeds.items()}, response_s=response)
    except ValueError as exc:
        raise RpcError(APP_ERROR, str(exc)) from None
    entry.results["approach"] = res
    max_px = int(_num(params, "max_px", _DISPLAY_PX, lo=200, hi=4096))
    overlay = map_render.zone_overlay(res.layer, _overlay_size(t, max_px), mode="approach")
    return {
        "breach": res.breach, "warning_m": _finite(res.warning_m),
        "warning_s": {k: _finite(v) for k, v in res.warning_s.items()},
        "response_s": res.response_s, "sufficient": res.sufficient,
        "breakout_xy": res.breakout_xy, "dash_path": res.dash_path, "breach_points": res.breach_points,
        "sectors": [{"x0": s.x0, "y0": s.y0, "x1": s.x1, "y1": s.y1, "warning_m": _finite(s.warning_m)}
                    for s in res.sectors],
        "covert_area_m2": res.covert_area_m2, "depth_m": res.depth_m, "notes": res.notes,
        "overlay": _image(overlay, max_px=max_px), "legend": map_render.legend("approach")[1],
        "measured": bool(t.is_measured),
    }


METHODS = {
    "approach.analyse": approach_analyse,
    "network.plan": network_plan,
    "perimeter.compare_models": compare_models,
    "perimeter.compare_use": compare_use,
}
