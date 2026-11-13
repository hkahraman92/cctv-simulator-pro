"""EN 62676-4 conformity statement for a PLAN (zone x level x result).

``compliance_report`` states how a camera MODEL answers a specification. This
states what a LAYOUT on a terrain delivers: for every zone the plan protects
(the band or polygon, and the fence line itself) and every DORI level, the
required pixel density, the share the fixed cameras hold continuously, the
share only a PTZ patrol reaches, and a verdict against the coverage goal.

Nothing is derived here: the grids come from the authoritative multi-camera
viewshed the coverage report already ran (``MultiViewshedResult``), the
thresholds from ``viewshed_3d.zone_ppm`` (EN 62676-4 for a visible camera,
Johnson for a thermal -- which has three levels and no face level). No Tk.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, List, Optional

import numpy as np

from .viewshed_3d import ZONE_DETECT, ZONE_IDENT, ZONE_OBSERVE, ZONE_RECOG, is_thermal_camera, zone_ppm

CLAUSE = "EN 62676-4 §6.2 / Tablo B.1"
CLAUSE_THERMAL = "Johnson ölçütü (EN 62676-4 DORI termal görüntüye uygulanmaz)"
_LEVELS = ((ZONE_DETECT, "detect", "Algılama"), (ZONE_OBSERVE, "observe", "Gözlem"),
           (ZONE_RECOG, "recognize", "Tanıma"), (ZONE_IDENT, "identify", "Teşhis"))
_LEVELS_THERMAL = ((ZONE_DETECT, "detect", "Johnson algılama"), (ZONE_OBSERVE, "observe", "Johnson tanıma"),
                   (ZONE_RECOG, "recognize", "Johnson teşhis"))
VERDICT_OK, VERDICT_PARTIAL, VERDICT_FAIL = "Uygun", "Kısmi", "Uygun değil"


def levels_for(camera) -> tuple:
    return _LEVELS_THERMAL if is_thermal_camera(camera) else _LEVELS


def declared_level(camera, target_ppm: float, area_level: Optional[int] = None) -> int:
    """The level the plan was built to hold: an area plan's own level, else the
    highest level whose threshold the fence target reaches (40 px/m -> Detection)."""
    zones = [z for z, _k, _l in levels_for(camera)]
    if area_level in zones:
        return int(area_level)
    thermal = is_thermal_camera(camera)
    held = [z for z in zones if zone_ppm(z, thermal) <= target_ppm + 1e-9]
    return max(held) if held else zones[0]


def _verdict(continuous: float, patrol: float, goal: float) -> str:
    if continuous >= goal - 1e-9:
        return VERDICT_OK
    if continuous + patrol >= goal - 1e-9:
        return VERDICT_PARTIAL                 # the goal is met only with intermittent (PTZ patrol) coverage
    return VERDICT_FAIL


def plan_conformity(plan, camera, mv, zones: List[Dict[str, Any]], *, cell_size_m: float,
                    ptz_zone: Optional[np.ndarray] = None, goal_frac: float = 0.95,
                    area_level: Optional[int] = None) -> Dict[str, Any]:
    """Rows of the statement.

    ``zones``: ``[{"name", "mask", "kind": "area" | "line", "size": m2 or m}]``
    -- the masks the coverage report scored. ``mv`` is that report's
    MultiViewshedResult; ``ptz_zone`` its best PTZ-patrol zone grid (or None)."""
    thermal = is_thermal_camera(camera)
    declared = declared_level(camera, float(plan.target_ppm), area_level)
    out_zones = []
    worst = VERDICT_OK
    order = {VERDICT_OK: 0, VERDICT_PARTIAL: 1, VERDICT_FAIL: 2}
    for zn in zones:
        mask = zn["mask"]
        n = int(mask.sum())
        rows = []
        for z, key, label in levels_for(camera):
            fixed = (mv.dori_grid >= z) & mask
            cont = float(fixed.sum()) / n if n else 0.0
            patrol = float(((ptz_zone >= z) & ~(mv.dori_grid >= z) & mask).sum()) / n \
                if (n and ptz_zone is not None) else 0.0
            verdict = _verdict(cont, patrol, goal_frac)
            rows.append({"level": key, "label": label, "required_ppm": zone_ppm(z, thermal), "continuous": cont,
                         "patrol": patrol, "verdict": verdict, "declared": z == declared})
            if z == declared and order[verdict] > order[worst]:
                worst = verdict
        out_zones.append({"name": zn["name"], "kind": zn["kind"], "size": float(zn["size"]), "rows": rows})
    name = next(lbl for z, _k, lbl in levels_for(camera) if z == declared)
    return {"zones": out_zones, "verdict": worst, "goal_frac": goal_frac, "thermal": thermal,
            "declared_level": next(k for z, k, _l in levels_for(camera) if z == declared), "declared_label": name,
            "declared_ppm": zone_ppm(declared, thermal), "clause": CLAUSE_THERMAL if thermal else CLAUSE,
            "has_patrol": ptz_zone is not None, "cell_size_m": cell_size_m}


def _pct(f: float) -> str:
    return "%" + f"{f * 100:.1f}".replace(".", ",")


def _num(v: float, nd: int = 0) -> str:
    return f"{v:,.{nd}f}".replace(",", " ").replace(".", ",")


def build_plan_statement(conf: Dict[str, Any], *, plan, camera, conditions: List[str], project_name: str = "",
                         bidder: str = "", measured: bool = True, now: Optional[_dt.datetime] = None) -> str:
    """One markdown document. ``conditions``: the lines that say under what
    the numbers hold (terrain source, surface model, weather, target height)."""
    when = (now or _dt.datetime.now()).strftime("%d.%m.%Y %H:%M")
    L: List[str] = ["# EN 62676-4 PLAN UYGUNLUK BEYANI", ""]
    L += [f"- **Proje:** {project_name or '—'}", f"- **Hazırlayan:** {bidder or '—'}", f"- **Tarih:** {when}",
          f"- **Kamera:** {getattr(camera, 'model_name', '') or getattr(camera, 'name', '') or '—'}",
          f"- **Dizilim:** {plan.pole_count} direk · {len(plan.placed_cameras)} sabit kamera"
          + (f" · {len(plan.ptz_cameras)} PTZ" if getattr(plan, "ptz_cameras", None) else ""),
          f"- **Beyan edilen seviye:** {conf['declared_label']} (≥ {_num(conf['declared_ppm'], 2).rstrip('0').rstrip(',')} px/m)"
          f" · kapsama hedefi {_pct(conf['goal_frac'])}",
          f"- **Dayanak:** {conf['clause']}", ""]
    if not measured:
        L += ["**UYARI — BAĞLAYICI DEĞİL:** arazi ölçülmüş bir yükseklik modeli değildir (temsili). Aşağıdaki "
              "sonuçlar bir tasarım ön değerlendirmesidir, uygunluk kanıtı olarak sunulamaz.", ""]
    # str.upper() turns "i" into a dotless "I" ("DEĞIL"): map it first
    L += [f"## Sonuç: {conf['verdict'].replace('i', 'İ').upper()}", ""]
    if conf["verdict"] == VERDICT_OK:
        L.append(f"Korunan bölgelerin hepsinde {conf['declared_label']} seviyesi, sabit kameralarla sürekli olarak "
                 f"kapsama hedefini tutmaktadır.")
    elif conf["verdict"] == VERDICT_PARTIAL:
        L.append(f"{conf['declared_label']} seviyesi kapsama hedefini yalnız PTZ devriyesiyle birlikte tutmaktadır; "
                 f"devriye kapsaması aralıklıdır (sürekli değildir).")
    else:
        L.append(f"En az bir korunan bölgede {conf['declared_label']} seviyesi kapsama hedefinin altındadır.")
    L += ["", "Kısaltmalar: U = Uygun · K = Kısmi (yalnız PTZ devriyesiyle) · X = Uygun değil", ""]
    mark = {VERDICT_OK: "U", VERDICT_PARTIAL: "K", VERDICT_FAIL: "X"}
    for zn in conf["zones"]:
        size = f"{_num(zn['size'] / 1e4, 2)} ha" if zn["kind"] == "area" else f"{_num(zn['size'])} m"
        L += [f"## Bölge: {zn['name']} ({size})", ""]
        head = "| Seviye | Gerekli px/m | Sürekli (sabit) |"
        sep = "|---|---|---|"
        if conf["has_patrol"]:
            head += " Aralıklı (PTZ devriyesi) |"
            sep += "---|"
        L += [head + " Sonuç |", sep + "---|"]
        for r in zn["rows"]:
            ppm = _num(r["required_ppm"], 2).rstrip("0").rstrip(",")
            line = f"| {r['label']}{' (beyan)' if r['declared'] else ''} | {ppm} | {_pct(r['continuous'])} |"
            if conf["has_patrol"]:
                line += f" {_pct(r['patrol'])} |"
            L.append(line + f" **{mark[r['verdict']]}** {r['verdict']} |")
        L.append("")
    L += ["## Geçerlilik koşulları", ""]
    L += [f"- {c}" for c in conditions if c]
    L += [f"- Analiz ızgarası {_num(conf['cell_size_m'], 1)} m; paylar bu çözünürlükte hücre sayımıdır.",
          "- Kapsama geometriktir (görüş hattı + kadraj + piksel yoğunluğu). Aydınlatma, gece / IR menzili, "
          "hareket bulanıklığı, sıkıştırma ve operatör algısı bu beyanın dışındadır.",
          "- Seviyeler bir üstünü içerir: Tanıma tutan hücre Gözlem ve Algılamayı da tutar.", "",
          "## Beyan", "",
          "Yukarıdaki tablolar, belirtilen kamera diziliminin korunan bölgelerde sağladığı piksel yoğunluğunu "
          "uygulamanın otoriter görüş alanı motoruyla, belirtilen koşullar altında hesaplanmış olarak sunar.", "",
          "| | |", "|---|---|", "| Hazırlayan | ______________________ |", "| Tarih / İmza | ______________________ |"]
    return "\n".join(L)


def write_plan_statement_pdf(path: str, markdown: str) -> None:
    from .exporters import write_simple_pdf
    lines = [ln.replace("**", "").replace("| ", "").replace(" |", "  ").replace("|", " ")
             for ln in markdown.splitlines() if not set(ln) <= set("|- ") or not ln.strip()]
    write_simple_pdf(path, "EN 62676-4 PLAN UYGUNLUK BEYANI", lines)
