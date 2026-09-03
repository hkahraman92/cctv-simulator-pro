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

    n_rows = sum(len(r.rows) for lst in results.values() for r in lst)
    print(
        f"{len(project.cameras)} kamera · mod {mode} · "
        f"{sum(len(v) for v in results.values())} optik sonuç · {n_rows} satır",
        file=sys.stderr,
    )

    if args.json:
        json.dump(_results_to_json(results), sys.stdout, ensure_ascii=False, indent=2)
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

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
