"""Headless entry point.

    py -3.13 -m cctv_simulator --project plan.json --export csv,xlsx,pdf --out ./rapor
    py -3.13 -m cctv_simulator --project plan.json --json > results.json

No Tk, no display. Runs the optic engine (calculations.calculate_for_camera —
the single source of truth) and the report writers in exporters.py. Use it for
CI smoke tests, batch report generation, and regression diffing.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Dict, List

from .calculations import analyze_dead_zone_coverage, calculate_for_camera
from .models import OpticResult
from .project_io import load_project

_EXPORTERS = {"csv", "xlsx", "pdf"}


def _build_terrain(project):
    from .terrain_loader import generate_procedural_terrain, load_geotiff_or_dem
    if project.terrain_source == "geotiff" and project.terrain_file:
        return load_geotiff_or_dem(project.terrain_file)
    grid = max(int(project.terrain_grid or 200), 32)
    return generate_procedural_terrain(
        project.terrain_preset, grid_size=grid,
        cell_size_m=max(project.terrain_width_m / grid, 1.0),
    )


def _run_viewshed(project, terrain, default_mode: str):
    """Combined multi-camera viewshed from the project's terrain.placements."""
    from .viewshed_3d import CameraPlacement, calculate_multi_camera_viewshed
    if not project.placements:
        raise ValueError(
            "Görüş alanı için proje dosyasında terrain.placements listesi gerekli. "
            'Her giriş: {"camera": <ad|index>, "x_m", "y_m", "mast_m", "pan_deg", "tilt_deg"}.')

    from .viewshed_3d import placement_bounds_warnings

    by_name = {c.name: c for c in project.cameras}
    placements = []
    for i, pl in enumerate(project.placements):
        ref = pl.get("camera", i)
        cam = by_name.get(ref) if isinstance(ref, str) else project.cameras[int(ref)]
        if cam is None:
            raise ValueError(f"terrain.placements[{i}]: '{ref}' kamerası projede yok.")
        lm = default_mode if default_mode in ("min", "max") else "min"
        placements.append(CameraPlacement(
            x_m=float(pl["x_m"]), y_m=float(pl["y_m"]),
            mast_height_m=float(pl.get("mast_m", cam.pole_height_m)),
            camera=cam, lens_mode=str(pl.get("lens_mode", lm)),
            pan_deg=float(pl.get("pan_deg", 0.0)),
            tilt_deg=float(pl.get("tilt_deg", -5.0)),
            max_range_m=float(pl.get("range_m", project.viewshed_range_m)),
            label=str(pl.get("label", cam.name)),
            focal_mm_override=(float(pl["focal_mm"]) if pl.get("focal_mm") else None),
        ))
    for warn in placement_bounds_warnings(terrain, placements):
        print(f"  ⚠ {warn}", file=sys.stderr)
    models = {p.camera.name for p in placements}
    rep_cam = placements[0].camera
    if len(models) > 1:
        print(f"  not: yerleşimler {len(models)} farklı kamera modeli kullanıyor; "
              f"rapor başlığında '{rep_cam.name}' gösteriliyor.", file=sys.stderr)
    return placements, rep_cam, calculate_multi_camera_viewshed(
        terrain, placements, visibility_km=_vis_km(project.weather), weather=project.weather)


def _vis_km(weather: str) -> float:
    from .atmosphere import WEATHER_PRESETS
    return WEATHER_PRESETS.get(weather, 40.0)


def _run_ptz(project, terrain, default_mode: str):
    from .ptz_tour import PTZPreset, PTZTour, evaluate_ptz_tour
    spec = project.ptz
    if not spec or not spec.get("presets"):
        raise ValueError(
            'PTZ analizi için proje dosyasında terrain.ptz.presets listesi gerekli. '
            'Her preset: {"name", "pan_deg", "tilt_deg", "lens_mode", "dwell_s"}.')
    by_name = {c.name: c for c in project.cameras}
    ref = spec.get("camera", 0)
    cam = by_name.get(ref) if isinstance(ref, str) else project.cameras[int(ref)]
    if cam is None:
        raise ValueError(f"terrain.ptz: '{ref}' kamerası projede yok.")
    lm = default_mode if default_mode in ("min", "max") else "max"
    tour = PTZTour(
        x_m=float(spec["x_m"]), y_m=float(spec["y_m"]),
        mast_height_m=float(spec.get("mast_m", cam.pole_height_m)),
        camera=cam, max_range_m=float(spec.get("range_m", project.viewshed_range_m)),
        slew_speed_deg_s=float(spec.get("slew_speed_deg_s", 90.0)),
        presets=[
            PTZPreset(
                name=str(p.get("name", f"P{i + 1}")),
                pan_deg=float(p["pan_deg"]), tilt_deg=float(p.get("tilt_deg", -5.0)),
                lens_mode=str(p.get("lens_mode", lm)), dwell_s=float(p.get("dwell_s", 5.0)),
            )
            for i, p in enumerate(spec["presets"])
        ],
    )
    return tour, evaluate_ptz_tour(terrain, tour, visibility_km=_vis_km(project.weather),
                                   weather=project.weather)


def _run(project, mode: str) -> Dict[str, List[OpticResult]]:
    modes = ["min", "max"] if mode == "compare" else [mode]
    results: Dict[str, List[OpticResult]] = {}
    for camera in project.cameras:
        results[camera.name] = [
            calculate_for_camera(camera, m, project.ppm_levels) for m in modes
        ]
    analyze_dead_zone_coverage(results, project.cameras)
    return results


def _results_to_json(results: Dict[str, List[OpticResult]]) -> dict:
    out: dict = {"cameras": {}}
    for name, res_list in results.items():
        out["cameras"][name] = [
            {
                "mode": r.mode,
                "focal_mm": r.focal_mm,
                "hfov_deg": r.hfov_deg,
                "vfov_deg": r.vfov_deg,
                "nominal_res_width_px": r.nominal_res_width_px,
                "effective_px_ratio": r.effective_px_ratio,
                "dead_zone_m": r.dead_zone_m,
                "dead_zone_area_m2": r.dead_zone_area_m2,
                "max_geom_dist_m": None if r.max_geom_dist_m == float("inf") else r.max_geom_dist_m,
                "dead_zone_covered_by": r.dead_zone_covered_by,
                "rows": [asdict(row) for row in r.rows],
            }
            for r in res_list
        ]
    return out


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cctv_simulator", description=__doc__.splitlines()[0])
    parser.add_argument("--project", required=True, type=Path, help="Proje .json dosyası")
    parser.add_argument(
        "--mode",
        choices=["min", "max", "compare"],
        default=None,
        help="Lens modu (varsayılan: proje dosyasındaki)",
    )
    parser.add_argument(
        "--export",
        default="",
        help="Virgülle ayrık: csv,xlsx,pdf",
    )
    parser.add_argument("--out", type=Path, default=Path("."), help="Çıktı klasörü")
    parser.add_argument("--json", action="store_true", help="Sonuçları stdout'a JSON yaz")
    parser.add_argument("--viewshed", action="store_true",
                        help="Arazi + terrain.placements'tan çoklu kamera birleşik görüş alanı hesapla ve rapora ekle")
    parser.add_argument("--ptz", action="store_true",
                        help="terrain.ptz preset turundan kapsama + revizit süresi analizi ekle")
    args = parser.parse_args(argv)

    if not args.project.is_file():
        parser.error(f"proje dosyası yok: {args.project}")

    formats = {f.strip().lower() for f in args.export.split(",") if f.strip()}
    unknown = formats - _EXPORTERS
    if unknown:
        parser.error(f"bilinmeyen format: {', '.join(sorted(unknown))} (geçerli: csv, xlsx, pdf)")

    project = load_project(args.project)
    mode = args.mode or project.lens_mode
    results = _run(project, mode)

    terrain = mv = placements = ptz_res = None
    report_cam = project.cameras[0]
    if args.viewshed or args.ptz:
        terrain = _build_terrain(project)
    if args.viewshed:
        placements, report_cam, mv = _run_viewshed(project, terrain, mode)
        print(f"görüş alanı · {len(placements)} kamera · birleşik kapsama "
              f"%{mv.coverage_pct:.1f} · örtüşen {mv.overlap_area_m2 / 1e6:.2f} km² · "
              f"arazi {'ÖLÇÜLMÜŞ' if terrain.is_measured else 'TEMSİLİ'}", file=sys.stderr)
    if args.ptz:
        _tour, ptz_res = _run_ptz(project, terrain, mode)
        if not args.viewshed:
            report_cam = _tour.camera
        print(f"PTZ turu · {len(ptz_res.preset_labels)} preset · periyot "
              f"{ptz_res.tour_period_s:.0f} sn · ort. revizit {ptz_res.mean_revisit_s:.0f} sn · "
              f"en kötü {ptz_res.worst_revisit_s:.0f} sn", file=sys.stderr)

    n_rows = sum(len(r.rows) for lst in results.values() for r in lst)
    print(
        f"{len(project.cameras)} kamera · mod {mode} · "
        f"{sum(len(v) for v in results.values())} optik sonuç · {n_rows} satır",
        file=sys.stderr,
    )

    if args.json:
        payload = _results_to_json(results)
        if ptz_res is not None:
            payload["ptz_tour"] = {
                "presets": ptz_res.preset_labels,
                "tour_period_s": round(ptz_res.tour_period_s, 1),
                "mean_revisit_s": round(ptz_res.mean_revisit_s, 1),
                "worst_revisit_s": round(ptz_res.worst_revisit_s, 1),
                "continuous_area_m2": round(ptz_res.continuous_area_m2, 1),
                "never_seen_area_m2": round(ptz_res.never_seen_area_m2, 1),
                "combined_coverage_pct": round(ptz_res.combined.coverage_pct, 2),
            }
        if mv is not None:
            payload["viewshed"] = {
                "terrain_measured": bool(terrain.is_measured),
                "weather": project.weather or "clear",
                "combined_coverage_pct": round(mv.coverage_pct, 2),
                "visible_area_m2": round(mv.visible_area_m2, 1),
                "occluded_area_m2": round(mv.occluded_area_m2, 1),
                "overlap_area_m2": round(mv.overlap_area_m2, 1),
                "pct_by_zone": {k: round(v, 2) for k, v in mv.pct_by_zone.items()},
                "cameras": [
                    {"label": lbl, "visible_area_m2": round(r.visible_area_m2, 1),
                     "max_los_reach_m": round(r.max_los_reach_m, 1),
                     "coverage_pct": round(r.coverage_pct, 2)}
                    for lbl, r in zip(mv.labels, mv.per_camera)
                ],
            }
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")

    if formats:
        from . import exporters

        args.out.mkdir(parents=True, exist_ok=True)
        stem = args.project.stem
        level_name = project.design_level or ""
        if "csv" in formats:
            p = args.out / f"{stem}.csv"
            exporters.export_csv(str(p), results)
            print(f"yazıldı: {p}", file=sys.stderr)
        if "xlsx" in formats:
            p = args.out / f"{stem}.xlsx"
            exporters.export_excel(str(p), project.cameras, mode, results, project.target_point, level_name)
            print(f"yazıldı: {p}", file=sys.stderr)
        if "pdf" in formats:
            p = args.out / f"{stem}.pdf"
            exporters.export_pdf(
                str(p), project.cameras, mode, results, project.target_point, level_name, ""
            )
            print(f"yazıldı: {p}", file=sys.stderr)

        if (args.viewshed or args.ptz) and formats & {"pdf", "csv"}:
            kw = dict(project_name=project.project_name or stem, terrain=terrain,
                      camera=report_cam, weather=project.weather, multi_viewshed=mv, ptz=ptz_res)
            if "pdf" in formats:
                vp = args.out / f"{stem}-gorusalani.pdf"
                exporters.export_engineering_report_pdf(str(vp), **kw)
                print(f"yazıldı: {vp}", file=sys.stderr)
            if "csv" in formats:
                vc = args.out / f"{stem}-gorusalani.csv"
                exporters.export_engineering_report_csv(str(vc), **kw)
                print(f"yazıldı: {vc}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
