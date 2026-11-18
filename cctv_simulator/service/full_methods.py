"""API v1: ``analysis.full`` -- one run, complete deliverables.

Runs the plan's analyses in the order a delivery needs them (coverage, fixed + PTZ, night, hidden
approach, network, data sensitivity, then the plan statement, acceptance sheet, engineering report and the
offer ZIP) with the parameter objects the desktop panels send to their own methods. Nothing is
re-derived here: every step CALLS the existing service function with a sub-context, so its result is
stored in ``entry.results`` exactly as if the panel had run it, and its numbers equal the separate call's.

A step is ``ok``, ``skipped`` (Turkish reason) or ``failed`` (Turkish error text); a failure never stops the
next step. Cancel is checked between steps and inside each step (its progress calls).
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import session as _s
from .protocol import APP_ERROR, INVALID_PARAMS, Cancelled, Context, RpcError

STEP_ORDER = ("coverage", "mixed", "night", "approach", "network", "sensitivity",
              "statement", "acceptance", "engineering", "package")
STEP_TITLES = {
    "coverage": "Birleşik kapsama", "mixed": "Sabit + PTZ değerlendirmesi", "night": "Gece kapsaması",
    "approach": "Gizli yaklaşma", "network": "Ağ planı ve maliyet", "sensitivity": "Veri hassasiyeti",
    "statement": "Plan uygunluk beyanı", "acceptance": "Kabul föyü", "engineering": "Mühendislik raporu",
    "package": "Teklif paketi (ZIP)",
}
# a panel's settings that are optional steps: absent / null / false = not requested
_OPTIONAL_REASON = {
    "night": "gece tarihi seçilmedi", "approach": "gizli yaklaşma analizi seçilmedi",
    "sensitivity": "veri hassasiyeti analizi seçilmedi",
}
_NO_FOLDER = "çıktı klasörü verilmedi (bu çıktı yalnız yazılır)"


class _Skip(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _step_cfg(steps: Dict[str, Any], key: str, *, default_on: bool) -> Optional[Dict[str, Any]]:
    """The step's parameter dict; None = not requested. Absent means ``default_on`` (an empty dict)."""
    if key not in steps:
        return {} if default_on else None
    v = steps[key]
    if v is None or v is False:
        return None
    if v is True:
        return {}
    if not isinstance(v, dict):
        raise RpcError(INVALID_PARAMS, f"'steps.{key}' bir nesne, true ya da false olmalı.")
    return v


def _sub_ctx(ctx: Context, index: int, total: int, title: str) -> Context:
    """A context for one step: its progress is folded into the whole run's."""
    def notify(_method: str, p: Dict[str, Any]) -> None:
        frac = (p["done"] / p["total"]) if p.get("total") else 0.0
        ctx.progress(index + min(max(frac, 0.0), 1.0), total, f"{title}: {p.get('message', '')}".rstrip(": "))
    return Context(ctx.request_id, ctx.token, notify)


_LEVEL_TR = {"monitor": "İzleme", "detect": "Algılama", "observe": "Gözlem", "recognize": "Tanıma", "identify": "Teşhis"}
_STATUS_TR = {"ok": "tamam", "skipped": "atlandı", "failed": "başarısız"}


def _shares(d: Dict[str, Any]) -> str:
    return ", ".join(f"{_LEVEL_TR.get(k, k)} {_pct(v)}" for k, v in d.items())


def _pct(v: Any) -> str:
    return "—" if not isinstance(v, (int, float)) else f"%{v * 100:.1f}".replace(".", ",")


def _num(v: Any, nd: int = 1) -> str:
    return "—" if not isinstance(v, (int, float)) else f"{v:,.{nd}f}".replace(",", " ").replace(".", ",")


# ── key numbers of each step ──────────────────────────────────────────────
def _numbers(step: str, p: Dict[str, Any]) -> Dict[str, Any]:
    if step == "coverage":
        t = p.get("target") or {}
        fl = p.get("fence_line") or {}
        return {"target_label": t.get("label"), "target_held": t.get("held"), "target_ptz_only": t.get("ptz_only"),
                "target_below": t.get("below"), "target_unseen": t.get("unseen"),
                "band_share": p.get("band_share"), "band_share_ptz": p.get("band_share_ptz"),
                "fence_line_held": fl.get("held"), "fence_line_uncovered_m": fl.get("uncovered_m"),
                "coverage_pct": p.get("coverage_pct"), "camera_count": p.get("camera_count"),
                "band_area_m2": p.get("band_area_m2"), "measured": p.get("measured")}
    if step == "mixed":
        return {k: p.get(k) for k in ("fixed_poles", "ptz_count", "continuous_detect_m2", "intermittent_detect_m2",
                                      "identifiable_pct", "median_d2i_s", "p90_d2i_s", "worst_d2i_s")}
    if step == "night":
        return {"night_share": p.get("night_band_share"), "day_share": p.get("day_band_share"),
                "mode": (p.get("mode") or {}).get("text"), "lux": (p.get("condition") or {}).get("lux")}
    if step == "approach":
        return {"breach": p.get("breach"), "warning_m": p.get("warning_m"), "warning_s": p.get("warning_s"),
                "response_s": p.get("response_s"), "sufficient": p.get("sufficient"),
                "covert_area_m2": p.get("covert_area_m2")}
    if step == "network":
        n, c = p.get("network") or {}, p.get("cost") or {}
        return {"switches": len(n.get("switches") or []), "copper_m": n.get("copper_m"), "fibre_m": n.get("fibre_m"),
                "storage_tb": n.get("storage_tb"), "cost_total": c.get("total"), "currency": c.get("currency"),
                "unpriced": len(c.get("unpriced") or [])}
    if step == "sensitivity":
        return {k: p.get(k) for k in ("certain", "uncertain", "never", "nominal", "n_runs", "level_label")}
    if step == "statement":
        return {"verdict": p.get("verdict"), "declared_label": p.get("declared_label"), "goal_frac": p.get("goal_frac"),
                "zones": len(p.get("zones") or []), "has_markdown": bool(p.get("markdown"))}
    if step == "acceptance":
        return {k: p.get(k) for k in ("cameras", "ptz", "points", "cameras_without_target_point", "uncertain_points")}
    return {}


def _stale(entry, step: str) -> bool:
    key = {"mixed": "mixed", "night": "night", "approach": "approach", "network": "network",
           "sensitivity": "sensitivity"}.get(step)
    return bool(key and key in entry.results)


def analysis_full(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Full analysis: coverage, fixed + PTZ, night, hidden approach, network, data sensitivity, plan statement,
    acceptance sheet, engineering report and offer ZIP in one job (per-step status; a failure does not stop the rest)."""
    from . import delivery_methods as dm
    from . import planning_methods as pm
    from . import ptz_methods as ptzm
    from . import report_methods as rm
    from . import sensitivity_methods as sm
    from . import terrain_methods as tm

    entry, t = tm._terrain_of(params)
    if "plan" not in entry.results:
        raise RpcError(APP_ERROR, "Önce bir çit / alan planı oluşturun.")
    plan, cam, kw, _fence = entry.results["plan"]
    if not plan.placed_cameras:
        raise RpcError(APP_ERROR, "Planda kamera yok.")
    steps = params.get("steps") or {}
    if not isinstance(steps, dict):
        raise RpcError(INVALID_PARAMS, "'steps' bir nesne olmalı.")
    out_dir: Optional[Path] = None
    if params.get("out_dir"):
        if not isinstance(params["out_dir"], str):
            raise RpcError(INVALID_PARAMS, "'out_dir' metin olmalı.")
        out_dir = rm._out_dir(params)
    stem = rm._stem(params)
    project_name = str(params.get("project_name") or stem)
    bidder = str(params.get("bidder", ""))

    common: Dict[str, Any] = {"terrain_id": entry.id, "use_obstacles": bool(params.get("use_obstacles", True))}
    for k in ("target_height_m", "band_m", "band_in_m"):
        if params.get(k) is not None:
            common[k] = params[k]
    cfg = {k: _step_cfg(steps, k, default_on=k in ("coverage", "mixed", "network", "statement", "acceptance",
                                                   "engineering", "package")) for k in STEP_ORDER}
    cov_cfg = cfg["coverage"] if cfg["coverage"] is not None else {}
    level = cov_cfg.get("level", "plan")
    total = len(STEP_ORDER)
    results: Dict[str, Any] = {}
    written: List[str] = []
    report: List[Dict[str, Any]] = []

    def p_of(step: str, **extra: Any) -> Dict[str, Any]:
        return {**common, **(cfg[step] or {}), **extra}

    # one function per step: returns (payload, files)
    def s_coverage(sc: Context):
        return tm.perimeter_coverage(p_of("coverage", level=level), sc), []

    def s_mixed(sc: Context):
        if not plan.ptz_cameras or kw.get("ptz_camera") is None:
            raise _Skip("plan PTZ içermiyor")
        return ptzm.perimeter_mixed(p_of("mixed"), sc), []

    def s_night(sc: Context):
        c = cfg["night"] or {}
        if not c.get("datetime"):
            raise _Skip(_OPTIONAL_REASON["night"])
        return dm.night_coverage(p_of("night"), sc), []

    def s_approach(sc: Context):
        return pm.approach_analyse(p_of("approach"), sc), []

    def s_network(sc: Context):
        return pm.network_plan(p_of("network"), sc), []

    def s_sensitivity(sc: Context):
        return sm.perimeter_sensitivity(p_of("sensitivity", **({"level": level} if "level" not in (cfg["sensitivity"] or {}) else {})), sc), []

    def s_statement(sc: Context):
        base = p_of("statement", project_name=project_name, bidder=bidder)
        base.pop("path", None)
        files: List[str] = []
        if out_dir is None:
            return dm.export_plan_statement(base, sc), files
        res = dm.export_plan_statement({**base, "path": str(out_dir / f"{stem}-plan-beyani.md")}, sc)
        files.append(res["path"])
        try:                                           # the PDF is a convenience: a missing reportlab never sinks the .md
            files.append(dm.export_plan_statement({**base, "path": str(out_dir / f"{stem}-plan-beyani.pdf")}, sc)["path"])
        except RpcError:
            pass
        return res, files

    def s_acceptance(sc: Context):
        base = p_of("acceptance", level=level)
        base.pop("path", None)
        if out_dir is None:                            # computed only: the same sheet the file would hold
            points = base.get("points_per_camera", 3)
            sheet = dm._acceptance_sheet(entry, t, base, sc, compute=True, points=int(points),
                                         tolerance=float(base.get("tolerance", 0.15)))
            return {**sheet.summary(), "path": None}, []
        files: List[str] = []
        res = None
        for fmt in ("pdf", "csv"):
            res = dm.delivery_acceptance({**base, "format": fmt, "path": str(out_dir / f"{stem}-kabul-foyu.{fmt}")}, sc)
            files.append(res["path"])
        return res, files

    def s_engineering(sc: Context):
        if out_dir is None:
            raise _Skip(_NO_FOLDER)
        res = rm.report_engineering({
            "terrain_id": entry.id, "use_obstacles": common["use_obstacles"], "formats": ["pdf", "csv"],
            "out_dir": str(out_dir), "stem": stem, "project_name": project_name,
            "camera": params.get("camera") or _camera_dict(cam), "weather": str(kw.get("weather", "")),
            **{k: v for k, v in (cfg["engineering"] or {}).items() if k in ("camera", "weather")}}, sc)
        return res, list(res["files"])

    def s_package(sc: Context):
        if out_dir is None:
            raise _Skip(_NO_FOLDER)
        res = dm.export_package({**p_of("package"), "path": str(out_dir / f"{stem}-teklif.zip"),
                                 "project_name": project_name, "bidder": bidder}, sc)
        return res, [res["path"]]

    runners: Dict[str, Callable[[Context], Tuple[Any, List[str]]]] = {
        "coverage": s_coverage, "mixed": s_mixed, "night": s_night, "approach": s_approach, "network": s_network,
        "sensitivity": s_sensitivity, "statement": s_statement, "acceptance": s_acceptance,
        "engineering": s_engineering, "package": s_package}

    # the run as it stands, kept in the session after every step: a cancelled / failed call returns nothing, and
    # the UI reads this (`analysis.full_partial`) to show the steps that did finish
    live: Dict[str, Any] = {"steps": report, "results": results, "written": written, "state": "running", "stopped_at": None}
    entry.results["full_run"] = live

    for i, step in enumerate(STEP_ORDER):
        title = STEP_TITLES[step]
        row: Dict[str, Any] = {"id": step, "title": title, "status": "ok", "elapsed_s": 0.0, "numbers": {}, "files": []}
        try:
            ctx.token.check()
            report.append(row)
            ctx.progress(i, total, title)
            if cfg[step] is None:
                row.update(status="skipped", reason=_OPTIONAL_REASON.get(step) or f"{title.lower()} seçilmedi")
                if _stale(entry, step):
                    row["kept_previous"] = True
                continue
            t0 = time.perf_counter()
            try:
                payload, files = runners[step](_sub_ctx(ctx, i, total, title))
                row["numbers"] = _numbers(step, payload)
                row["files"] = files
                written.extend(files)
                results[step] = payload
            except Cancelled:
                raise
            except _Skip as sk:
                row.update(status="skipped", reason=sk.reason)
                if _stale(entry, step):
                    row["kept_previous"] = True
            except RpcError as exc:
                row.update(status="failed", error=exc.message)
            except Exception as exc:  # an engine bug in one step must not lose the others
                row.update(status="failed", error=f"İç hata: {type(exc).__name__}: {exc}")
            row["elapsed_s"] = time.perf_counter() - t0
        except Cancelled:
            if not report or report[-1] is not row:
                report.append(row)
            row.update(status="cancelled", reason="iptal edildi")
            live.update(state="cancelled", stopped_at=step)
            raise
    live["state"] = "done"
    ctx.progress(total, total, "Tamamlandı")

    out: Dict[str, Any] = {
        "steps": report, "written": written, "out_dir": str(out_dir) if out_dir else None,
        "measured": bool(t.is_measured), "refine_factor": entry.refine_factor, "target_height_m": entry.target_height_m,
        "total_s": sum(r["elapsed_s"] for r in report),
        "counts": {k: sum(1 for r in report if r["status"] == k) for k in ("ok", "skipped", "failed")},
        "results": results,
    }
    if out_dir is not None:
        md = out_dir / "analiz-ozeti.md"
        try:
            md.write_text(summary_markdown(entry, t, plan, kw, out, project_name), encoding="utf-8")
            out["summary_path"] = str(md)
            written.append(str(md))
        except OSError as exc:
            out["summary_error"] = f"Özet yazılamadı: {exc}"
    return out


def _camera_dict(cam) -> Dict[str, Any]:
    import dataclasses
    return dataclasses.asdict(cam)


# ── analiz-ozeti.md ───────────────────────────────────────────────────────
def _line_for(row: Dict[str, Any]) -> List[str]:
    n, sid = row["numbers"], row["id"]
    if sid == "coverage":
        bs = n.get("band_share") or {}
        lines = [f"Hedef ({n.get('target_label')}) sabit kameralarla tutulan pay: {_pct(n.get('target_held'))}",
                 f"Yalnız PTZ ile: {_pct(n.get('target_ptz_only'))} · hedefin altında: {_pct(n.get('target_below'))} · görülmeyen: {_pct(n.get('target_unseen'))}",
                 "Bant payları: " + _shares(bs)]
        if n.get("fence_line_held") is not None:
            lines.append(f"Çit çizgisi payı: {_pct(n['fence_line_held'])}; kapsanmayan {_num(n.get('fence_line_uncovered_m'), 0)} m")
        return lines
    if sid == "mixed":
        return [f"Teşhis edilebilir pay: {_num(n.get('identifiable_pct'))} % · tespit→teşhis ortanca {_num(n.get('median_d2i_s'))} sn"]
    if sid == "night":
        ns = n.get("night_share") or {}
        return ["Gece bant payı: " + _shares(ns), f"Mod: {n.get('mode')}"]
    if sid == "approach":
        return [f"İhlal: {'evet' if n.get('breach') else 'hayır'} · uyarı mesafesi {_num(n.get('warning_m'), 0)} m · müdahale süresi yeterli: "
                f"{'evet' if n.get('sufficient') else 'hayır'}"]
    if sid == "network":
        return [f"Switch {n.get('switches')} · bakır {_num(n.get('copper_m'), 0)} m · fiber {_num(n.get('fibre_m'), 0)} m · "
                f"kayıt {_num(n.get('storage_tb'))} TB · " + (
                    f"maliyet: {n.get('unpriced')} kalemin birim fiyatı girilmedi (toplam verilmez)" if n.get("unpriced")
                    else f"maliyet {_num(n.get('cost_total'), 0)} {n.get('currency') or ''}".rstrip())]
    if sid == "sensitivity":
        return [f"Kesin {_pct(n.get('certain'))} · belirsiz {_pct(n.get('uncertain'))} · hiç {_pct(n.get('never'))} ({n.get('n_runs')} koşu)"]
    if sid == "acceptance":
        return [f"{n.get('points')} kontrol noktası, {n.get('cameras')} kamera; hedef noktası olmayan kamera: {n.get('cameras_without_target_point')}"]
    return []


def summary_markdown(entry, t, plan, kw: Dict[str, Any], out: Dict[str, Any], project_name: str) -> str:
    """The run as a short Turkish record: every step with status / reason / numbers, and the assumptions."""
    mark = {"ok": "●", "skipped": "–", "failed": "■"}
    L = [f"# Tam analiz özeti — {project_name}", "",
         f"Plan: {plan.camera_count} kamera, {plan.pole_count} direk · çizgi modu: {plan.line_mode}", "",
         "## Adımlar", ""]
    for r in out["steps"]:
        head = f"- {mark[r['status']]} **{r['title']}** — {_STATUS_TR[r['status']]}"
        if r["status"] == "skipped":
            head += f": {r.get('reason', '')}" + (" (önceki sonuç korunuyor)" if r.get("kept_previous") else "")
        elif r["status"] == "failed":
            head += f": {r.get('error', '')}"
        else:
            head += f" ({r['elapsed_s']:.1f} sn)".replace(".", ",")
        L.append(head)
        if r["status"] == "ok":
            L.extend(f"  - {x}" for x in _line_for(r))
            L.extend(f"  - dosya: {Path(f).name}" for f in r["files"])
    L += ["", "## Varsayımlar", ""]
    L.append("- Arazi: " + ("ölçülmüş yükseklik modeli" if t.is_measured else "TEMSİLİ (ölçüm değil; bağlayıcı değil)")
             + (f" — {t.source_note}" if getattr(t, "source_note", "") else ""))
    L.append(f"- Analiz ızgarası inceltme: {entry.refine_factor}× (hücre {t.cell_size_m:.1f} m)".replace(".", ",", 1))
    L.append(f"- Hedef boyu: {entry.target_height_m:.1f} m".replace(".", ",") if entry.target_height_m > 0
             else "- Hedef zemin noktasıdır (insan boyu modellenmedi)")
    L.append(f"- Hava: {kw.get('weather') or 'Berrak hava'} (görüş {kw.get('visibility_km', 40.0):g} km)")
    sens = entry.results.get("sensitivity")
    if sens is not None and out["results"].get("sensitivity") is not None:
        L.append("- Veri hassasiyeti varsayımları: " + str(sens.get("line", "")))
        a = sens.get("assumptions") or {}
        if a:
            L.append(f"  - DEM σ {_num(a.get('dem_sigma_m'))} m, korelasyon {_num(a.get('dem_corr_m'), 0)} m, "
                     f"engel ölçeği {_num(a.get('obstacle_scale'))}")
    L += ["", "Sayılar otoriter optik / görüş alanı motorundan gelir; bu özet onları yeniden türetmez.", ""]
    return "\n".join(L)


def analysis_full_partial(params: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """The last ``analysis.full`` run as it stands in the session (cheap read): the steps that finished with their
    results, the one a cancel stopped (status ``cancelled``) and the rest as ``not_run``. ``run`` is null when no run
    exists (never run, or a new plan dropped it)."""
    from . import terrain_methods as tm

    entry, t = tm._terrain_of(params)
    live = entry.results.get("full_run")
    if live is None:
        return {"run": None}
    steps = [dict(r) for r in live["steps"]]
    seen = {r["id"] for r in steps}
    for sid in STEP_ORDER:
        if sid not in seen:
            steps.append({"id": sid, "title": STEP_TITLES[sid], "status": "not_run", "reason": "çalışmadı", "elapsed_s": 0.0,
                          "numbers": {}, "files": []})
    keys = ("ok", "skipped", "failed", "cancelled", "not_run")
    return {"run": {
        "steps": steps, "written": list(live["written"]), "out_dir": None, "measured": bool(t.is_measured),
        "refine_factor": entry.refine_factor, "target_height_m": entry.target_height_m,
        "total_s": sum(r["elapsed_s"] for r in steps), "counts": {k: sum(1 for r in steps if r["status"] == k) for k in keys},
        "results": dict(live["results"]), "state": live["state"], "stopped_at": live["stopped_at"]}}


METHODS = {"analysis.full": analysis_full, "analysis.full_partial": analysis_full_partial}
