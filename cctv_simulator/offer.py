"""Offer deliverables: scenario summary, a read-only shareable HTML page, and the one-click offer package (ZIP).

Everything here is assembly: numbers come from the engine results that were
already run (plan, network / cost, combined viewshed, night run) and the
existing writers (engineering report, compliance statement, KML / DXF). A
summary field that was not computed is ``None`` and is written as "—", never
estimated here.
"""
from __future__ import annotations

import base64
import csv
import datetime as _dt
import html
import io
import json
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .terrain_loader import TerrainData

LEVEL_NAMES = (("identify", "Teşhis"), ("recognize", "Tanıma"), ("observe", "Gözlem"), ("detect", "Algılama"))


# ------------------------------------------------------------------ scenario summary
def scenario_summary(plan, *, label: str = "", network=None, cost=None, band_share: Optional[Dict] = None,
                     night: Optional[Dict] = None, terrain: Optional[TerrainData] = None,
                     camera_model: str = "") -> Dict[str, Any]:
    """A flat, JSON-able record of one design alternative (what the comparison table shows)."""
    fixed = list(plan.placed_cameras)
    ptz = list(getattr(plan, "ptz_cameras", []) or [])
    out: Dict[str, Any] = {
        "label": label or "Senaryo",
        "created": _dt.datetime.now().isoformat(timespec="seconds"),
        "camera_model": camera_model or (fixed[0].camera_model if fixed else ""),
        "ptz_model": ptz[0].camera_model if ptz else "",
        "poles": plan.pole_count, "fixed_cameras": len(fixed), "ptz_cameras": len(ptz),
        "fence_m": round(plan.total_fence_length_m, 1),
        "avg_spacing_m": round(plan.avg_spacing_m, 1),
        "fence_coverage_pct": round(plan.coverage_percentage, 2),
        "gap_m": round(sum(g.length_m for g in plan.gaps), 1),
        "target_ppm": plan.target_ppm, "line_mode": plan.line_mode,
        "bitrate_mbps": None, "storage_tb": None, "switches": None, "copper_m": None, "fibre_m": None,
        "poe_w": None, "cost_total": None, "currency": "", "unpriced": [],
        "band_share": {k: None for k, _ in LEVEL_NAMES}, "night_band_share": None, "night_mode": "",
        "measured": None if terrain is None else bool(terrain.is_measured),
    }
    if network is not None:
        out.update(bitrate_mbps=round(network.bitrate_mbps, 1), storage_tb=round(network.storage_tb, 2),
                   switches=len(network.switches), copper_m=round(network.copper_m), fibre_m=round(network.fibre_m),
                   poe_w=round(network.poe_total_w))
    else:
        out.update(bitrate_mbps=round(plan.estimated_bandwidth_mbps, 1),
                   storage_tb=round(plan.estimated_storage_30days_tb, 2))
    if cost is not None:
        out.update(cost_total=round(cost.total, 2), currency=cost.currency, unpriced=list(cost.unpriced))
    if band_share:
        out["band_share"] = {k: (None if band_share.get(k) is None else round(float(band_share[k]), 4))
                             for k, _ in LEVEL_NAMES}
    if night:
        out["night_band_share"] = {k: round(float(v), 4) for k, v in (night.get("band_share") or {}).items()}
        out["night_mode"] = str(night.get("mode_text", ""))
    return out


def _fmt(v, nd=0, pct=False, unit=""):
    if v is None:
        return "—"
    if pct:
        return f"%{v * 100:.1f}".replace(".", ",")
    s = f"{v:,.{nd}f}".replace(",", " ").replace(".", ",")
    return f"{s} {unit}".strip()


def _cost_cell(s: Dict) -> str:
    total = s.get("cost_total")
    if total is None:
        return "—"
    if not total and s.get("unpriced"):
        return "fiyat girilmedi"
    return _fmt(total, 0, unit=s.get("currency", "")) + (" (eksik fiyat)" if s.get("unpriced") else "")


def comparison_rows(scenarios: Sequence[Dict]) -> List[List[str]]:
    """Table (header + rows) of scenarios side by side; first column is the metric."""
    rows = [["Ölçüt"] + [s.get("label", f"S{i + 1}") for i, s in enumerate(scenarios)]]

    def add(name, fn):
        rows.append([name] + [fn(s) for s in scenarios])

    add("Kamera modeli", lambda s: s.get("camera_model") or "—")
    add("Direk", lambda s: _fmt(s.get("poles")))
    add("Sabit / PTZ", lambda s: f"{s.get('fixed_cameras', 0)} / {s.get('ptz_cameras', 0)}")
    add("Ortalama aralık", lambda s: _fmt(s.get("avg_spacing_m"), 1, unit="m"))
    add("Çit kapsaması", lambda s: _fmt(None if s.get("fence_coverage_pct") is None else s["fence_coverage_pct"] / 100,
                                        pct=True))
    for k, nm in LEVEL_NAMES:
        add(f"Bant {nm}", lambda s, k=k: _fmt((s.get("band_share") or {}).get(k), pct=True))
    add("Gece bant Algılama", lambda s: _fmt((s.get("night_band_share") or {}).get("detect"), pct=True))
    add("PoE switch", lambda s: _fmt(s.get("switches")))
    add("Bant genişliği", lambda s: _fmt(s.get("bitrate_mbps"), 1, unit="Mbps"))
    add("Kayıt", lambda s: _fmt(s.get("storage_tb"), 1, unit="TB"))
    add("Toplam maliyet", _cost_cell)
    return rows


# ------------------------------------------------------------------ tables
def pole_rows(plan) -> List[List]:
    head = ["Direk", "Tür", "X (m)", "Y (m)", "Zemin (m)", "Direk boyu (m)", "Model", "Odak (mm)", "HFOV (°)",
            "Yön (°)", "Eğim (°)", "Menzil (m)", "Kör bölge (m)", "Devriye (°)"]
    out = [head]
    for c, kind in [(c, "Sabit") for c in plan.placed_cameras] + \
                   [(c, "PTZ") for c in (getattr(plan, "ptz_cameras", []) or [])]:
        out.append([c.pole_id, kind, round(c.x_m, 2), round(c.y_m, 2), round(c.ground_z_m, 2),
                    round(c.mast_height_m, 2), c.camera_model, round(c.focal_mm, 2), round(c.hfov_deg, 2),
                    round(c.pan_deg % 360, 1), round(c.tilt_deg, 2), round(c.effective_range_m, 1),
                    round(c.dead_zone_m, 1), round(getattr(c, "patrol_span_deg", 0.0), 0) if kind == "PTZ" else ""])
    return out


def switch_rows(network) -> List[List]:
    out = [["Switch", "X (m)", "Y (m)", "Çit üzerinde (m)", "Direkler", "Port", "PoE (W)", "Bakır (m)", "Uyarı"]]
    for s in network.switches:
        out.append([f"SW{s.index}", round(s.x_m, 1), round(s.y_m, 1), round(s.arc_m, 1),
                    " ".join(f"D{p}" for p in s.poles), s.ports_used, round(s.poe_w, 1), round(s.copper_m, 1),
                    "PoE bütçesi aşıldı" if s.over_budget else ""])
    return out


def cost_rows(cost) -> List[List]:
    out = [["Kalem", "Miktar", "Birim", f"Birim fiyat ({cost.currency})", f"Tutar ({cost.currency})"]]
    for ln in cost.lines:
        out.append([ln.item, ln.qty, ln.unit, ln.unit_price if ln.unit_price > 0 else "FİYAT YOK", ln.total])
    out.append(["TOPLAM (fiyatlı kalemler)", "", "", "", round(cost.total, 2)])
    return out


def _csv_bytes(rows: Sequence[Sequence], preamble: Sequence[str] = ()) -> bytes:
    buf = io.StringIO()
    for line in preamble:
        buf.write(f"# {line}\n")
    w = csv.writer(buf, delimiter=";")
    for r in rows:
        w.writerow(r)
    return ("﻿" + buf.getvalue()).encode("utf-8")


def _xlsx_bytes(sheets: Dict[str, Sequence[Sequence]]) -> Optional[bytes]:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font
    except ImportError:
        return None
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name[:31])
        for r in rows:
            ws.append(list(r))
        for c in ws[1]:
            c.font = Font(bold=True)
        for col in ws.columns:
            width = max(len(str(c.value or "")) for c in col)
            ws.column_dimensions[col[0].column_letter].width = min(max(width + 2, 8), 48)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ------------------------------------------------------------------ shareable HTML
def _css_vars(theme: str) -> str:
    """CSS custom properties from the design tokens (one colour source for both UIs and the page)."""
    from .design_tokens import tok
    pairs = {"bg": "bg-0", "bg2": "bg-1", "fg": "ink", "mut": "ink-2", "acc": "accent", "warn": "warn",
             "danger": "danger", "info": "info", "line": "line", "fov": "dori-observe", "ptz": "brand-hi",
             "fence": "warn"}
    return ";".join(f"--{k}:{tok(v, theme)}" for k, v in pairs.items())


def _png_b64(img, fmt="PNG", quality=85) -> str:
    buf = io.BytesIO()
    if fmt == "JPEG":
        img.convert("RGB").save(buf, "JPEG", quality=quality)
    else:
        img.save(buf, "PNG", optimize=True)
    return f"data:image/{fmt.lower()};base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def build_share_html(terrain: TerrainData, plan, *, title: str, network=None, coverage=None, photo=None,
                     summary: Optional[Dict] = None, scenarios: Sequence[Dict] = (), max_px: int = 1600) -> str:
    """One self-contained, read-only HTML page: map (base + coverage + vectors), key figures, comparison.
    No script, no external request: it opens offline in any browser and can be mailed as is."""
    from PIL import Image

    from . import map_render
    from .geo_export import wedge

    W, H = terrain.width_m, terrain.height_m
    k = max_px / max(W, H)
    size = (max(1, round(W * k)), max(1, round(H * k)))
    base = map_render.relief_image(terrain, "hybrid" if photo is not None else "elevation", photo, 0.85,
                                   palette="console").resize(size)
    layer = map_render.obstacle_layer(terrain, size, photo is not None)
    if layer is not None:
        base = Image.alpha_composite(base.convert("RGBA"), layer)
    base_uri = _png_b64(base, "JPEG")
    cov_uri = ""
    legend_html = ""
    if coverage is not None:
        cov_uri = _png_b64(map_render.zone_overlay(coverage.dori_grid, size))
        _t, items = map_render.legend("dori")
        legend_html = "".join(f'<li><i style="background:rgba({r},{g},{b},{a / 255:.2f})"></i>{html.escape(txt)}</li>'
                              for txt, (r, g, b, a) in items)

    def P(x, y):
        return f"{x:.1f},{H - y:.1f}"

    sw = max(W, H) / 400.0
    svg = []
    if plan is not None:
        for c in plan.placed_cameras:
            ring = wedge(c.x_m, c.y_m, c.pan_deg, c.hfov_deg, c.effective_range_m, c.dead_zone_m, n=16)
            svg.append(f'<polygon class="fov" points="{" ".join(P(x, y) for x, y in ring)}"/>')
        for c in getattr(plan, "ptz_cameras", []) or []:
            span = getattr(c, "patrol_span_deg", 0.0) or 360.0
            ring = wedge(c.x_m, c.y_m, c.pan_deg, span, c.effective_range_m, n=48)
            svg.append(f'<polygon class="ptz" points="{" ".join(P(x, y) for x, y in ring)}"/>')
        svg.append(f'<polyline class="fence" points="{" ".join(P(x, y) for x, y in plan.fence_points)}"/>')
        for g in plan.gaps:
            svg.append(f'<line class="gap" x1="{g.start_x:.1f}" y1="{H - g.start_y:.1f}" x2="{g.end_x:.1f}" '
                       f'y2="{H - g.end_y:.1f}"><title>Boşluk {g.length_m:.0f} m</title></line>')
        seen = set()
        for c in list(plan.placed_cameras) + list(getattr(plan, "ptz_cameras", []) or []):
            if c.pole_id in seen:
                continue
            seen.add(c.pole_id)
            tip = html.escape(f"D{c.pole_id} · {c.camera_model} · {c.focal_mm:.1f} mm · {c.mast_height_m:.0f} m direk · "
                              f"menzil {c.effective_range_m:.0f} m")
            svg.append(f'<circle class="pole" cx="{c.x_m:.1f}" cy="{H - c.y_m:.1f}" r="{sw * 2.2:.2f}">'
                       f'<title>{tip}</title></circle>')
    if network is not None:
        for s in network.switches:
            a = sw * 3
            svg.append(f'<rect class="sw" x="{s.x_m - a:.1f}" y="{H - s.y_m - a:.1f}" width="{2 * a:.1f}" '
                       f'height="{2 * a:.1f}"><title>SW{s.index} · {s.ports_used} port · {s.poe_w:.0f} W</title></rect>')

    figs = []
    if summary:
        bs = summary.get("band_share") or {}
        figs = [("Direk", _fmt(summary.get("poles"))),
                ("Kamera", f"{summary.get('fixed_cameras', 0)} sabit"
                 + (f" + {summary['ptz_cameras']} PTZ" if summary.get("ptz_cameras") else "")),
                ("Çit", _fmt(summary.get("fence_m"), 0, unit="m")),
                ("Bant Algılama", _fmt(bs.get("detect"), pct=True)),
                ("Bant Teşhis", _fmt(bs.get("identify"), pct=True)),
                ("Kayıt", _fmt(summary.get("storage_tb"), 1, unit="TB")),
                ("Maliyet", _cost_cell(summary))]
    fig_html = "".join(f"<div class=fig><b>{html.escape(v)}</b><span>{html.escape(k_)}</span></div>"
                       for k_, v in figs)
    table_html = ""
    if scenarios:
        rows = comparison_rows(scenarios)
        table_html = ("<h2>Senaryo karşılaştırması</h2><table><thead><tr>"
                      + "".join(f"<th>{html.escape(c)}</th>" for c in rows[0]) + "</tr></thead><tbody>"
                      + "".join("<tr>" + "".join(f"<td>{html.escape(str(c))}</td>" for c in r) + "</tr>"
                                for r in rows[1:]) + "</tbody></table>")
    measured = bool(terrain.is_measured)
    src = html.escape(terrain.source_note or terrain.name or "")
    warn = ("" if measured else '<p class="warn">UYARI — Arazi ölçülmüş değil (temsili). Kapsama sonuçları '
            'bağlayıcı değildir.</p>')
    when = _dt.datetime.now().strftime("%d.%m.%Y %H:%M")
    return f"""<!doctype html>
<html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<style>
:root{{{_css_vars("dark")}}}
@media (prefers-color-scheme: light){{:root{{{_css_vars("light")}}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,Segoe UI,sans-serif}}
main{{max-width:1200px;margin:0 auto;padding:16px}}h1{{font-size:22px;margin:4px 0}}h2{{font-size:17px;margin:24px 0 8px}}
.sub{{color:var(--mut);font-size:13px}}.warn{{background:rgba(240,164,58,.15);border-left:4px solid var(--warn);padding:8px 12px}}
.figs{{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px;margin:12px 0}}
.fig{{background:var(--bg2);border:1px solid var(--line);border-radius:6px;padding:10px}}.fig b{{display:block;font-size:19px}}
.fig span{{color:var(--mut);font-size:12px}}
.map{{position:relative;width:100%;aspect-ratio:{W:.1f}/{H:.1f};border:1px solid var(--line);border-radius:6px;overflow:hidden}}
.map img,.map svg{{position:absolute;inset:0;width:100%;height:100%}}
.fov{{fill:var(--fov);fill-opacity:.18;stroke:var(--fov);stroke-opacity:.8;stroke-width:{sw * .6:.2f}}}
.ptz{{fill:var(--ptz);fill-opacity:.10;stroke:var(--ptz);stroke-width:{sw * .8:.2f};stroke-dasharray:{sw * 3:.1f}}}
.fence{{fill:none;stroke:var(--fence);stroke-width:{sw * 1.5:.2f}}}.gap{{stroke:var(--danger);stroke-width:{sw * 3:.2f}}}
.pole{{fill:var(--acc);stroke:var(--fg);stroke-width:{sw * .6:.2f}}}.sw{{fill:var(--info);stroke:var(--fg);stroke-width:{sw * .5:.2f}}}
ul.leg{{list-style:none;padding:0;display:flex;flex-wrap:wrap;gap:6px 16px;font-size:13px}}
ul.leg i{{display:inline-block;width:14px;height:14px;border-radius:3px;margin-right:6px;vertical-align:-2px}}
.tw{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;font-size:13px}}
th,td{{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;white-space:nowrap}}
th{{background:var(--bg2)}}footer{{color:var(--mut);font-size:12px;margin:24px 0}}
</style></head><body><main>
<h1>{html.escape(title)}</h1><div class="sub">{src} · {when} · salt okunur paylaşım</div>
{warn}<div class="figs">{fig_html}</div>
<div class="map"><img src="{base_uri}" alt="Arazi">{f'<img src="{cov_uri}" alt="Kapsama">' if cov_uri else ""}
<svg viewBox="0 0 {W:.1f} {H:.1f}" preserveAspectRatio="none">{"".join(svg)}</svg></div>
<ul class="leg">{legend_html}<li><i style="background:var(--fence)"></i>Çit</li><li><i style="background:var(--acc)"></i>Direk</li>
<li><i style="background:var(--info)"></i>PoE switch</li><li><i style="background:var(--danger)"></i>Kapsama boşluğu</li></ul>
<div class="tw">{table_html}</div>
<footer>Kapsama EN 62676-4 DORI eşikleri ile görüş alanı motorundan; harita görüntüsü yalnız gösterimdir, alan ve
yüzdeler hesaptan gelir. CCTV Simülatör Pro.</footer>
</main></body></html>"""


# ------------------------------------------------------------------ offer package
def build_offer_package(zip_path: str | Path, *, project_name: str, terrain: TerrainData, plan, camera,
                        network=None, cost=None, coverage=None, band_share: Optional[Dict] = None,
                        night: Optional[Dict] = None, photo=None, spec_result: Optional[Dict] = None,
                        spec_kw: Optional[Dict] = None, scenarios: Sequence[Dict] = (), weather: str = "",
                        progress=None) -> Dict[str, Any]:
    """Write the offer ZIP; returns {"path", "files": [names], "skipped": [reasons]}."""
    import tempfile

    from . import exporters, geo_export

    def step(i, msg):
        if progress:
            progress(i, 9, msg)

    files: Dict[str, bytes] = {}
    skipped: List[str] = []
    summary = scenario_summary(plan, label=project_name, network=network, cost=cost, band_share=band_share,
                               night=night, terrain=terrain, camera_model=getattr(camera, "model_name", ""))
    measured_line = ("Arazi: ölçülmüş DEM" if terrain.is_measured
                     else "UYARI: arazi ölçülmüş değil (temsili) — kapsama sonuçları bağlayıcı değil")
    pre = [project_name, measured_line, terrain.source_note or ""]

    step(0, "Metraj ve maliyet")
    sheets: Dict[str, Sequence[Sequence]] = {"Direkler": pole_rows(plan)}
    files["02-direk-listesi.csv"] = _csv_bytes(pole_rows(plan), pre)
    if network is not None:
        sheets["Ağ planı"] = switch_rows(network)
        files["03-ag-plani.csv"] = _csv_bytes(switch_rows(network),
                                              pre + [*network.assumptions, *("UYARI: " + w for w in network.warnings)])
    else:
        skipped.append("Ağ planı çalıştırılmadı (Kablo / ağ planı paneli)")
    if cost is not None:
        sheets = {"Maliyet": cost_rows(cost), **sheets}
        extra = [f"Fiyatı girilmemiş kalemler toplamın dışında: {', '.join(cost.unpriced)}"] if cost.unpriced else []
        files["01-metraj-maliyet.csv"] = _csv_bytes(cost_rows(cost), pre + extra)
    else:
        skipped.append("Maliyet hesaplanmadı (birim fiyat girilmedi)")
    xlsx = _xlsx_bytes(sheets)
    if xlsx:
        files["01-metraj.xlsx"] = xlsx

    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        step(1, "Mühendislik raporu")
        kw = dict(project_name=project_name, terrain=terrain, camera=camera, weather=weather, perimeter=plan,
                  multi_viewshed=coverage)
        try:
            exporters.export_engineering_report_pdf(str(tdp / "r.pdf"), **kw)
            files["04-muhendislik-raporu.pdf"] = (tdp / "r.pdf").read_bytes()
            exporters.export_engineering_report_csv(str(tdp / "r.csv"), **kw)
            files["04-muhendislik-raporu.csv"] = (tdp / "r.csv").read_bytes()
        except Exception as exc:                            # noqa: BLE001 -- one part must not sink the package
            skipped.append(f"Mühendislik raporu yazılamadı: {exc}")
        step(2, "Uygunluk beyanı")
        if spec_result:
            from .compliance_report import build_statement, write_statement_pdf
            skw = dict(spec_kw or {})
            files["05-uygunluk-beyani.md"] = build_statement(spec_result, **skw).encode("utf-8")
            try:
                write_statement_pdf(str(tdp / "s.pdf"), spec_result, **skw)
                files["05-uygunluk-beyani.pdf"] = (tdp / "s.pdf").read_bytes()
            except Exception as exc:                        # noqa: BLE001
                skipped.append(f"Uygunluk beyanı PDF'i yazılamadı: {exc}")
        else:
            skipped.append("Şartname analizi yok — uygunluk beyanı eklenmedi")
        step(3, "KMZ / DXF")
        try:
            geo_export.write_kml(tdp / "s.kmz", terrain, plan, network, coverage=coverage, name=project_name)
            files["06-saha.kmz"] = (tdp / "s.kmz").read_bytes()
        except ValueError as exc:
            skipped.append(f"KMZ yok: {exc}")
        files["06-saha.dxf"] = geo_export.build_dxf(terrain, plan, network).replace("\n", "\r\n").encode("ascii")
        if geo_export.GeoFrame(terrain).exact:
            files["06-saha-projeksiyon.dxf"] = geo_export.build_dxf(terrain, plan, network, real_world=True) \
                .replace("\n", "\r\n").encode("ascii")
    step(4, "Paylaşım sayfası")
    files["07-paylasim.html"] = build_share_html(terrain, plan, title=project_name, network=network, coverage=coverage,
                                                 photo=photo, summary=summary, scenarios=scenarios).encode("utf-8")
    files["08-ozet.json"] = json.dumps({"summary": summary, "scenarios": list(scenarios)}, ensure_ascii=False,
                                       indent=2).encode("utf-8")
    readme = [f"{project_name} — teklif paketi", _dt.datetime.now().strftime("%d.%m.%Y %H:%M"), "", measured_line, "",
              "İçerik:"] + [f"  {n}" for n in sorted(files)] + ["", "Eklenmeyenler:"] + \
             ([f"  - {s}" for s in skipped] or ["  (yok)"]) + \
             ["", "Sayılar uygulamanın görüş alanı / optik motorundan gelir; 07-paylasim.html internet bağlantısız açılır."]
    files["00-BENIOKU.txt"] = "\r\n".join(readme).encode("utf-8-sig")
    step(5, "ZIP yazılıyor")
    p = Path(zip_path)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(files):
            z.writestr(name, files[name])
    tmp.replace(p)
    return {"path": str(p), "files": sorted(files), "skipped": skipped, "summary": summary}
