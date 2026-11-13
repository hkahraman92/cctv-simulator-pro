"""API v1: deliverables and scenarios -- KML / KMZ / DXF, night scenario, scenario
snapshot + comparison, shareable HTML page and the one-click offer package.

All of them read the analyses already run on a terrain (``entry.results``:
plan, network, cost, multi, band_share, night); none re-derives physics.
"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any, Dict, List, Optional

from .params import _camera_from, _require
from .protocol import APP_ERROR, INVALID_PARAMS, Context, RpcError
from .terrain_methods import _DISPLAY_PX, _image, _num, _overlay_size, _terrain_of

_LEVEL_KEYS = ("identify", "recognize", "observe", "detect")


def _plan_of(entry):
    if "plan" not in entry.results:
        raise RpcError(APP_ERROR, "Önce bir çit planı oluşturun.")
    return entry.results["plan"]


def _target(params: Dict[str, Any], key: str, suffixes) -> Path:
    p = Path(_require(params, key, str))
    if p.suffix.lower() not in suffixes:
        raise RpcError(INVALID_PARAMS, f"'{key}' uzantısı şunlardan biri olmalı: {', '.join(suffixes)}")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RpcError(APP_ERROR, f"Klasör oluşturulamadı: {exc}") from None
    return p


# ── KML / KMZ / DXF ──────────────────────────────────────────────────────
def export_site(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Plan (+ network, + combined coverage in KMZ) as .kml / .kmz / .dxf."""
    from .. import geo_export

    entry, t = _terrain_of(params)
    plan = _plan_of(entry)[0]
    p = _target(params, "path", (".kml", ".kmz", ".dxf"))
    net = entry.results.get("network")
    privacy = entry.results.get("privacy")      # city analysis ran: KVKK mask polygons
    try:
        if p.suffix.lower() == ".dxf":
            geo_export.write_dxf(p, t, plan, net, real_world=bool(params.get("real_world", False)), privacy=privacy)
        else:
            geo_export.write_kml(p, t, plan, net, coverage=entry.results.get("multi"),
                                 name=str(params.get("name") or p.stem), privacy=privacy)
    except ValueError as exc:
        raise RpcError(APP_ERROR, str(exc)) from None
    frame = geo_export.GeoFrame(t)
    return {"path": str(p), "exact_georef": frame.exact, "has_latlon": frame.has_latlon,
            "privacy_masks": sum(len(m["rings"]) for m in privacy or []),
            "with_network": net is not None, "with_coverage": p.suffix.lower() == ".kmz" and "multi" in entry.results}


# ── night ────────────────────────────────────────────────────────────────
def _when(params: Dict[str, Any]) -> _dt.datetime:
    raw = _require(params, "datetime", str)
    try:
        when = _dt.datetime.fromisoformat(raw)
    except ValueError:
        raise RpcError(INVALID_PARAMS, "'datetime' YYYY-AA-GGTSS:DD biçiminde yerel saat olmalı.") from None
    return when.replace(tzinfo=None)


def _latlon(params: Dict[str, Any], t=None):
    if params.get("lat") is not None and params.get("lon") is not None:
        return _num(params, "lat", lo=-90, hi=90), _num(params, "lon", lo=-180, hi=180)
    if t is not None and t.lat_center is not None:
        return float(t.lat_center), float(t.lon_center)
    raise RpcError(APP_ERROR, "Bu arazinin coğrafi konumu yok: gece senaryosu için enlem / boylam girin.")


def _light_kw(params: Dict[str, Any]) -> Dict[str, Any]:
    off = params.get("utc_offset_h")
    return {"utc_offset_h": None if off is None else _num(params, "utc_offset_h", lo=-14, hi=14),
            "cloud_cover": _num(params, "cloud_cover", 0.0, lo=0, hi=1),
            "sky_glow_lux": _num(params, "sky_glow_lux", 0.0, lo=0, hi=50)}


def _mode_for(cam, lux: float, params: Dict[str, Any]):
    from ..night import camera_night_mode
    return camera_night_mode(cam.sensor_name, cam.model_name, cam.min_lux, cam.ir_range_m, lux,
                             ir_on=bool(params.get("ir_on", True)),
                             white_light_range_m=_num(params, "white_light_range_m", 0.0, lo=0, hi=2000))


def _cond_json(c) -> Dict[str, Any]:
    return {"when": c.when_local.isoformat(timespec="minutes"), "sun_el_deg": c.sun_el_deg, "sun_az_deg": c.sun_az_deg,
            "moon_el_deg": c.moon_el_deg, "moon_az_deg": c.moon_az_deg, "moon_fraction": c.moon_fraction,
            "lux": c.lux, "period": c.period}


def night_condition(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Ambient light at a local time and what it means for a camera."""
    from ..night import light_condition

    t = None
    if params.get("terrain_id"):
        t = _terrain_of(params)[1]
    lat, lon = _latlon(params, t)
    c = light_condition(lat, lon, _when(params), **_light_kw(params))
    out = {"condition": _cond_json(c), "lat": lat, "lon": lon}
    if isinstance(params.get("camera"), dict):
        m = _mode_for(_camera_from(params["camera"]), c.lux, params)
        out["mode"] = {"mode": m.mode, "range_cap_m": m.range_cap_m, "text": m.text, "verified": m.verified}
    return out


def night_timeline(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Light from local noon to the next noon, and the camera's night mode at each step."""
    from ..night import night_timeline as timeline

    t = _terrain_of(params)[1] if params.get("terrain_id") else None
    lat, lon = _latlon(params, t)
    try:
        date = _dt.date.fromisoformat(_require(params, "date", str))
    except ValueError:
        raise RpcError(INVALID_PARAMS, "'date' YYYY-AA-GG olmalı.") from None
    step = int(_num(params, "step_min", 30, lo=5, hi=120))
    cam = _camera_from(params["camera"]) if isinstance(params.get("camera"), dict) else None
    rows = []
    for c in timeline(lat, lon, date, step_min=step, **_light_kw(params)):
        r = _cond_json(c)
        if cam is not None:
            m = _mode_for(cam, c.lux, params)
            r.update(mode=m.mode, range_cap_m=m.range_cap_m)
        rows.append(r)
    return {"lat": lat, "lon": lon, "steps": rows}


def night_coverage(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """The plan's fixed poles at a night time: the engine with each pole's reach
    capped by what lights the scene, against the same band by day."""
    from .. import map_render
    from ..focal_optimizer import band_area
    from ..night import capped_placements, light_condition
    from .terrain_methods import _band_params, _plan_roi
    from ..viewshed_3d import ZONE_DETECT, ZONE_IDENT, ZONE_OBSERVE, ZONE_RECOG

    entry, t = _terrain_of(params)
    plan, cam, kw, fence = _plan_of(entry)
    if not plan.placed_cameras:
        raise RpcError(APP_ERROR, "Planda sabit kamera yok.")
    if isinstance(params.get("camera"), dict):          # IR / min-lux edited in the panel
        cam = _camera_from(params["camera"])
    lat, lon = _latlon(params, t)
    cond = light_condition(lat, lon, _when(params), **_light_kw(params))
    mode = _mode_for(cam, cond.lux, params)
    is_area = kw.get("line_mode") == "area"
    band, inside = (0.0, None) if is_area else _band_params(params)
    roi = _plan_roi(t, fence, kw, band, inside)
    cell = t.cell_size_m ** 2
    roi_area = float(roi.sum() * cell)
    levels = (("identify", ZONE_IDENT), ("recognize", ZONE_RECOG), ("observe", ZONE_OBSERVE), ("detect", ZONE_DETECT))

    def shares(mv):
        if mv is None or not roi_area:
            return {k: 0.0 for k, _ in levels}
        return {k: float(((mv.dori_grid >= z) & roi).sum() * cell) / roi_area for k, z in levels}

    ctx.progress(0, 2, "Gündüz kapsaması")
    day_mv, _ = band_area(t, plan, cam, roi, ZONE_DETECT, visibility_km=kw["visibility_km"], weather=kw["weather"])
    ctx.progress(1, 2, "Gece kapsaması")
    night_mv, _ = band_area(t, plan, cam, roi, ZONE_DETECT, visibility_km=kw["visibility_km"], weather=kw["weather"],
                            transform=lambda pls: capped_placements(pls, mode))
    day, night = shares(day_mv), shares(night_mv)
    res = {"band_share": night, "day_band_share": day, "mode_text": mode.text, "when": cond.when_local.isoformat()}
    entry.results["night"] = res
    max_px = int(_num(params, "max_px", _DISPLAY_PX, lo=200, hi=4096))
    out = {"condition": _cond_json(cond), "mode": {"mode": mode.mode, "range_cap_m": mode.range_cap_m, "text": mode.text,
                                                   "verified": mode.verified},
           "band_m": band, "band_area_m2": roi_area, "roi_kind": "area" if is_area else "band",
           "day_band_share": day, "night_band_share": night,
           "legend": map_render.legend("dori")[1], "measured": bool(t.is_measured)}
    if night_mv is not None:
        out["overlay"] = _image(map_render.zone_overlay(night_mv.dori_grid, _overlay_size(t, max_px)), max_px=max_px)
    return out


# ── scenarios ────────────────────────────────────────────────────────────
def _summary(entry, t, label: str):
    from ..offer import scenario_summary
    plan, cam, _kw, _f = _plan_of(entry)
    bs = entry.results.get("band_share")
    return scenario_summary(plan, label=label, network=entry.results.get("network"), cost=entry.results.get("cost"),
                            band_share=bs[1] if bs else None, night=entry.results.get("night"), terrain=t,
                            camera_model=cam.model_name)


def scenario_snapshot(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """The current plan + whatever was run on it, as one comparable record."""
    entry, t = _terrain_of(params)
    return {"scenario": _summary(entry, t, str(params.get("label") or "Senaryo"))}


def _scenarios(params: Dict[str, Any], key: str = "scenarios", required: bool = True) -> List[Dict]:
    sc = params.get(key)
    if sc is None and not required:
        return []
    if not isinstance(sc, list) or (required and not sc) or not all(isinstance(s, dict) for s in sc):
        raise RpcError(INVALID_PARAMS, f"'{key}' senaryo nesnelerinin listesi olmalı.")
    if len(sc) > 12:
        raise RpcError(INVALID_PARAMS, "En fazla 12 senaryo karşılaştırılabilir.")
    return sc


def scenario_compare(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Side-by-side table of saved scenarios (and which one is best on each metric)."""
    from ..offer import comparison_rows
    sc = _scenarios(params)
    best: Dict[str, Optional[int]] = {}

    def pick(key, fn, lower=True):
        vals = [(fn(s), i) for i, s in enumerate(sc)]
        vals = [(v, i) for v, i in vals if v is not None]
        best[key] = (min(vals) if lower else max(vals))[1] if len(vals) > 1 else None

    pick("poles", lambda s: s.get("poles"))
    pick("cost_total", lambda s: None if s.get("unpriced") else s.get("cost_total"))
    for k in _LEVEL_KEYS:
        pick(f"band_{k}", lambda s, k=k: (s.get("band_share") or {}).get(k), lower=False)
    pick("night_detect", lambda s: (s.get("night_band_share") or {}).get("detect"), lower=False)
    pick("storage_tb", lambda s: s.get("storage_tb"))
    return {"rows": comparison_rows(sc), "best": best}


# ── share page / offer package ───────────────────────────────────────────
def export_share_html(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Self-contained read-only HTML page of the plan (no script, no network)."""
    from ..offer import build_share_html

    entry, t = _terrain_of(params)
    plan = _plan_of(entry)[0]
    p = _target(params, "path", (".html", ".htm"))
    title = str(params.get("title") or p.stem)
    text = build_share_html(t, plan, title=title, network=entry.results.get("network"),
                            coverage=entry.results.get("multi"), photo=entry.photo, summary=_summary(entry, t, title),
                            scenarios=_scenarios(params, required=False))
    p.write_text(text, encoding="utf-8")
    return {"path": str(p), "bytes": len(text.encode("utf-8"))}


def export_package(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """One ZIP: BOM + cost (CSV / XLSX), pole and switch lists, engineering report,
    compliance statement (if a spec result is passed), KMZ + DXF, share page, summary."""
    from ..offer import build_offer_package

    entry, t = _terrain_of(params)
    plan, cam, kw, _f = _plan_of(entry)
    p = _target(params, "path", (".zip",))
    spec = params.get("spec_result")
    if spec is not None and not isinstance(spec, dict):
        raise RpcError(INVALID_PARAMS, "'spec_result' bir nesne olmalı.")
    spec_kw = {"project_name": str(params.get("project_name", "")), "bidder": str(params.get("bidder", ""))}
    bs = entry.results.get("band_share")
    stmt = _plan_statement(entry, t, params)
    try:
        out = build_offer_package(
            p, project_name=str(params.get("project_name") or p.stem), terrain=t, plan=plan, camera=cam,
            network=entry.results.get("network"), cost=entry.results.get("cost"), coverage=entry.results.get("multi"),
            band_share=bs[1] if bs else None, night=entry.results.get("night"), photo=entry.photo,
            spec_result=spec, spec_kw=spec_kw, plan_statement=stmt[1] if stmt else None, ptz_camera=kw.get("ptz_camera"), scenarios=_scenarios(params, required=False),
            weather=kw.get("weather", ""), progress=ctx.progress)
    except OSError as exc:
        raise RpcError(APP_ERROR, f"Paket yazılamadı: {exc}") from None
    return out


# ── plan conformity statement ─────────────────────────────────────────────
def _plan_statement(entry, t, params: Dict[str, Any]):
    """(conformity dict, markdown) from the coverage report already run; None if it was not."""
    from ..exporters import _surface_line
    from ..plan_statement import build_plan_statement, plan_conformity

    plan, cam, kw, _f = _plan_of(entry)
    masks, mv = entry.results.get("coverage_masks"), entry.results.get("multi")
    if masks is None or mv is None:
        return None
    mode = kw.get("line_mode")
    cell = t.cell_size_m ** 2
    band, inside = masks["band"], masks["inside"]
    if mode == "area":
        name = "Korunan alan (poligon)"
    elif mode == "border":
        name = f"İzlenen şerit ({'sol' if kw.get('watch_side') == 'left' else 'sağ'} taraf, {band:.0f} m derinlik)"
    elif inside is not None and inside != band:
        name = f"Çit bandı (dış {band:.0f} m · iç {inside:.0f} m)"
    else:
        name = f"Çit bandı (±{band:.0f} m)"
    zones = [{"name": name, "mask": masks["roi"], "kind": "area", "size": float(masks["roi"].sum()) * cell}]
    if masks["line"] is not None and mode != "border":      # a border pole looks across the line by design
        zones.append({"name": "Çit çizgisi", "mask": masks["line"], "kind": "line",
                      "size": float(plan.total_fence_length_m)})
    conf = plan_conformity(plan, cam, mv, zones, cell_size_m=t.cell_size_m, ptz_zone=masks["ptz_zone"],
                           goal_frac=_num(params, "goal_frac", 0.95, lo=0.3, hi=1.0),
                           area_level=kw.get("level") if mode == "area" else None)
    weather = kw.get("weather") or "Berrak hava"
    conditions = [
        ("Arazi: ölçülmüş yükseklik modeli" if t.is_measured else "Arazi: TEMSİLİ (ölçüm değil)")
        + (f" — {t.source_note}" if getattr(t, "source_note", "") else ""),
        _surface_line(t).strip() or "Yüzey modeli yok: binalar ve ağaçlar görüşü engellemez (çıplak arazi).",
        f"Hava: {weather} (görüş {kw.get('visibility_km', 40.0):g} km).",
        (f"Hedef boyu {entry.target_height_m:.1f} m: görüş hattı ve kadraj başa göre.".replace(".", ",", 1)
         if entry.target_height_m > 0 else "Hedef zemin noktasıdır (insan boyu modellenmedi)."),
    ]
    md = build_plan_statement(conf, plan=plan, camera=cam, conditions=conditions, measured=bool(t.is_measured),
                              project_name=str(params.get("project_name", "")), bidder=str(params.get("bidder", "")))
    return conf, md


def export_plan_statement(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """EN 62676-4 conformity statement of the PLAN: zone x level x result, from the coverage
    report already run. Returns the rows and the markdown; writes ``path`` (.md / .pdf) if given."""
    from ..plan_statement import write_plan_statement_pdf

    entry, t = _terrain_of(params)
    got = _plan_statement(entry, t, params)
    if got is None:
        raise RpcError(APP_ERROR, "Önce Birleşik Kapsamayı hesaplayın: beyan onun sonuçlarından yazılır.")
    conf, md = got
    out = {**conf, "markdown": md, "measured": bool(t.is_measured), "path": None}
    if params.get("path"):
        p = _target(params, "path", (".md", ".pdf"))
        try:
            if p.suffix.lower() == ".pdf":
                write_plan_statement_pdf(str(p), md)
            else:
                p.write_text(md, encoding="utf-8")
        except OSError as exc:
            raise RpcError(APP_ERROR, f"Beyan yazılamadı: {exc}") from None
        out["path"] = str(p)
    return out


METHODS = {
    "export.plan_statement": export_plan_statement,
    "export.site": export_site,
    "export.share_html": export_share_html,
    "export.package": export_package,
    "night.condition": night_condition,
    "night.timeline": night_timeline,
    "night.coverage": night_coverage,
    "scenario.snapshot": scenario_snapshot,
    "scenario.compare": scenario_compare,
}
