"""Core service: report files written by the same exporters as the classic app / CLI."""
from __future__ import annotations

import dataclasses
import io
import json

from cctv_simulator.models import CameraConfig
from cctv_simulator.service import session
from cctv_simulator.service.methods import METHODS
from cctv_simulator.service.protocol import INVALID_PARAMS, Server


def call(method, **params):
    out = io.StringIO()
    server = Server(METHODS, out=out)
    server.handle_line(json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}))
    server.wait_idle()
    return next(json.loads(x) for x in out.getvalue().splitlines() if json.loads(x).get("id") == 1)


def ok(method, **params):
    r = call(method, **params)
    assert "result" in r, r.get("error")
    return r["result"]


CAMS = [dataclasses.asdict(CameraConfig(name="Giriş")), dataclasses.asdict(CameraConfig(name="Otopark", focal_max_mm=50))]


def test_optics_report_writes_every_format(tmp_path):
    r = ok("report.optics", cameras=CAMS, mode="compare", formats=["csv", "xlsx", "pdf"],
           out_dir=str(tmp_path), stem="Proje: A/B")
    names = sorted(p.split("\\")[-1].split("/")[-1] for p in r["files"])
    assert names == ["Proje- A-B.csv", "Proje- A-B.pdf", "Proje- A-B.xlsx"]       # path-unsafe chars replaced
    assert (tmp_path / "Proje- A-B.pdf").read_bytes().startswith(b"%PDF-")
    assert "Otopark" in (tmp_path / "Proje- A-B.csv").read_text(encoding="utf-8-sig")


def test_optics_report_rejects_duplicate_names_and_bad_formats(tmp_path):
    dup = [CAMS[0], CAMS[0]]
    assert call("report.optics", cameras=dup, formats=["csv"], out_dir=str(tmp_path))["error"]["code"] == INVALID_PARAMS
    assert call("report.optics", cameras=CAMS, formats=["docx"], out_dir=str(tmp_path))["error"]["code"] == INVALID_PARAMS


def test_engineering_report_needs_an_analysis_then_carries_the_measured_flag(tmp_path):
    session.reset()
    t = ok("terrain.procedural", preset="canyon", grid=80, width_m=800.0)
    tid = t["terrain_id"]
    cam = CAMS[0]
    err = call("report.engineering", terrain_id=tid, camera=cam, formats=["pdf"], out_dir=str(tmp_path))
    assert "henüz analiz yok" in err["error"]["message"]
    ok("viewshed.single", terrain_id=tid, camera=cam, x_m=400, y_m=400, mast_m=8, pan_deg=0, tilt_deg=-3, range_m=300)
    r = ok("report.engineering", terrain_id=tid, camera=cam, formats=["pdf", "csv"], out_dir=str(tmp_path), stem="saha")
    assert r["sections"] == {"viewshed": True, "coverage": False, "perimeter": False} and r["measured"] is False
    csv_text = (tmp_path / "saha-gorusalani.csv").read_text(encoding="utf-8-sig")
    assert "ölçüm değil" in csv_text and "GÖRÜŞ ALANI" in csv_text
    assert (tmp_path / "saha-gorusalani.pdf").read_bytes().startswith(b"%PDF-")


def test_open_refuses_non_report_files(tmp_path):
    exe = tmp_path / "x.exe"
    exe.write_bytes(b"MZ")
    assert call("report.open", path=str(exe))["error"]["code"] == INVALID_PARAMS
