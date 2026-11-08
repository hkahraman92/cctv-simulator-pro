"""Smoke-test a built cctv-core.exe over stdio: every heavy path once.

    py -3.13 desktop/packaging/smoke_core.py desktop/src-tauri/core/cctv-core.exe

A frozen bundle can lack a module the source tree has (a lazy import
PyInstaller missed, a Tk import in an engine module): this catches it before
the installer ships.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

CAM = {"name": "Duman", "focal_min_mm": 2.8, "focal_max_mm": 12.0}


def _cog(path: Path) -> bool:
    """A tiled, deflate + floating-point-predictor GeoTIFF (UTM 36N): the
    frozen core must read it through tifffile + imagecodecs."""
    try:
        import numpy as np
        import tifffile
    except ImportError:
        return False
    z = (np.add.outer(np.arange(64), np.arange(64)) + 800.0).astype("float32")
    keys = (1, 1, 0, 3, 1024, 0, 1, 1, 1025, 0, 1, 1, 3072, 0, 1, 32636)
    tifffile.imwrite(path, z, tile=(32, 32), compression="zlib", predictor=3, extratags=[
        (33550, 12, 3, (10.0, 10.0, 0.0), False), (33922, 12, 6, (0.0, 0.0, 0.0, 480000.0, 4420000.0, 0.0), False),
        (34735, 3, 16, keys, False)])
    return True


def main(exe: str) -> int:
    out_dir = tempfile.mkdtemp(prefix="cctv-smoke-")
    cog = Path(out_dir) / "cog.tif"
    has_cog = _cog(cog)
    calls = [
        ("system.describe", {}),
        ("library.list", {}),
        ("optics.calculate", {"camera": CAM}),
        ("terrain.procedural", {"preset": "canyon", "grid": 80, "width_m": 800}),
        ("terrain.image", {"terrain_id": "t1", "mode": "hybrid", "max_px": 300}),
        ("viewshed.single", {"terrain_id": "t1", "camera": CAM, "x_m": 400, "y_m": 400, "range_m": 300, "max_px": 300}),
        ("perimeter.plan", {"terrain_id": "t1", "camera": CAM, "fence": [[200, 200], [600, 200], [600, 600]]}),
        ("perimeter.coverage", {"terrain_id": "t1", "max_px": 300}),
        ("ptz.sweep", {"terrain_id": "t1", "camera": CAM, "x_m": 400, "y_m": 400, "range_m": 300, "max_px": 300}),
        ("scene.render", {"camera": CAM, "width": 320, "height": 180}),
        ("optics.bench", {"camera": CAM, "req_ppm": 125, "distance_m": 10}),
        ("spec.analyze", {"text": "Madde 1: 30 m mesafede teşhis sağlanmalıdır.", "engine": "rule"}),
        ("report.optics", {"cameras": [CAM], "formats": ["pdf", "xlsx", "csv"], "out_dir": out_dir, "stem": "duman"}),
        ("report.engineering", {"terrain_id": "t1", "camera": CAM, "formats": ["pdf"], "out_dir": out_dir, "stem": "duman"}),
        ("network.plan", {"terrain_id": "t1", "prices": {"camera": 1000}}),
        ("night.coverage", {"terrain_id": "t1", "datetime": "2024-01-11T23:00", "lat": 39.92, "lon": 32.85, "max_px": 300}),
        ("scenario.snapshot", {"terrain_id": "t1", "label": "A"}),
        ("export.site", {"terrain_id": "t1", "path": str(Path(out_dir) / "duman.dxf")}),
        ("export.package", {"terrain_id": "t1", "path": str(Path(out_dir) / "duman.zip"), "project_name": "Duman"}),
    ]
    if has_cog:
        calls.append(("terrain.geotiff", {"path": str(cog)}))
    # one request at a time: the server works on two in parallel, and the
    # terrain calls depend on the terrain created by an earlier one
    proc = subprocess.Popen([exe], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace")
    replies = {}
    for i, (m, p) in enumerate(calls):
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": i, "method": m, "params": p}) + "\n")
        proc.stdin.flush()
        while True:
            line = proc.stdout.readline()
            if not line:
                break
            msg = json.loads(line)
            if msg.get("id") == i:
                replies[i] = msg
                break
    proc.stdin.close()
    stderr = proc.stderr.read()
    proc.wait(timeout=60)
    bad = 0
    for i, (m, _p) in enumerate(calls):
        r = replies.get(i)
        if r is None or "error" in r:
            bad += 1
            print(f"FAIL {m}: {r and r['error']['message']}")
        else:
            print(f"ok   {m}")
    if has_cog:
        r = replies.get(len(calls) - 1, {}).get("result", {})
        if not r.get("is_measured"):
            bad += 1
            print("FAIL terrain.geotiff: COG not read as a measured DEM")
    for f in ("duman.pdf", "duman.xlsx", "duman.csv", "duman-gorusalani.pdf", "duman.dxf", "duman.zip"):
        if not (Path(out_dir) / f).is_file():
            bad += 1
            print(f"FAIL file {f}")
    if bad:
        print(stderr[-3000:])
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
