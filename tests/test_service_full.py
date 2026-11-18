"""analysis.full through the core service: one run, per-step status, numbers equal the separate calls."""
from __future__ import annotations

import dataclasses
import io
import json
import zipfile
from pathlib import Path

import pytest

from cctv_simulator.models import CameraConfig
from cctv_simulator.service import session
from cctv_simulator.service.full_methods import STEP_ORDER, analysis_full
from cctv_simulator.service.methods import METHODS
from cctv_simulator.service.protocol import Cancelled, CancelToken, Context, Server

CAM = dataclasses.asdict(CameraConfig(sensor_name='1/2.8"', resolution_name="4 MP (2K - 2688x1520)",
                                      focal_min_mm=2.8, focal_max_mm=12.0, ir_range_m=30.0, min_lux=0.05))
PTZ = dataclasses.asdict(CameraConfig(name="PTZ", focal_min_mm=4.8, focal_max_mm=153.0,
                                      resolution_name="2 MP (1080p - 1920x1080)"))
FENCE = [[300, 300], [900, 300], [900, 900], [300, 900]]
NIGHT = {"datetime": "2024-01-11T23:00", "lat": 39.92, "lon": 32.85, "utc_offset_h": 3, "max_px": 300, "camera": CAM}
STEPS = {"coverage": {"max_px": 300}, "mixed": {"max_px": 300}, "night": NIGHT,
         "approach": {"depth_m": 200, "response_s": 60, "max_px": 300},
         "network": {"switch_ports": 8, "retention_days": 30},
         "sensitivity": {"n_runs": 4, "max_px": 300}}
ALL_OK = ("coverage", "mixed", "night", "approach", "network", "sensitivity", "statement", "acceptance")


def call(method, **params):
    out = io.StringIO()
    server = Server(METHODS, out=out)
    server.handle_line(json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}))
    server.wait_idle()
    return next(m for m in map(json.loads, out.getvalue().splitlines()) if m.get("id") == 1)


def ok(method, **params):
    r = call(method, **params)
    assert "result" in r, r.get("error")
    return r["result"]


def make(ptz: bool = True) -> str:
    session.reset()
    tid = ok("terrain.procedural", preset="ridge_and_valley", grid=100, width_m=1000.0)["terrain_id"]
    extra = dict(ptz_camera=PTZ, ptz_every_n=4, ptz_patrol_span_deg=360) if ptz else {}
    ok("perimeter.plan", terrain_id=tid, camera=CAM, fence=FENCE, target_ppm=40, mast_m=6, **extra)
    return tid


def by_id(res):
    return {s["id"]: s for s in res["steps"]}


def test_full_run_without_folder_equals_the_separate_calls():
    tid = make()
    common = dict(terrain_id=tid, use_obstacles=True)
    cov = ok("perimeter.coverage", **common, max_px=300)
    mixed = ok("perimeter.mixed", **common, max_px=300)
    night = ok("night.coverage", **common, **NIGHT)
    appr = ok("approach.analyse", **common, depth_m=200, response_s=60, max_px=300)
    net = ok("network.plan", **common, switch_ports=8, retention_days=30)
    sens = ok("perimeter.sensitivity", **common, n_runs=4, max_px=300)

    res = ok("analysis.full", **common, steps=STEPS)
    st = by_id(res)
    assert [s["id"] for s in res["steps"]] == list(STEP_ORDER)
    assert {k: st[k]["status"] for k in ALL_OK} == {k: "ok" for k in ALL_OK}
    assert st["engineering"]["status"] == "skipped" and "klasör" in st["engineering"]["reason"]
    assert st["package"]["status"] == "skipped" and res["written"] == [] and res["out_dir"] is None
    n = lambda k: st[k]["numbers"]  # noqa: E731
    assert n("coverage")["target_held"] == cov["target"]["held"]
    assert n("coverage")["band_share"] == cov["band_share"]
    assert n("coverage")["fence_line_held"] == cov["fence_line"]["held"]
    assert n("mixed")["median_d2i_s"] == mixed["median_d2i_s"]
    assert n("mixed")["identifiable_pct"] == mixed["identifiable_pct"]
    assert n("night")["night_share"] == night["night_band_share"]
    assert n("approach")["warning_m"] == appr["warning_m"] and n("approach")["breach"] == appr["breach"]
    assert n("network")["cost_total"] == net["cost"]["total"]
    assert n("network")["switches"] == len(net["network"]["switches"])
    assert (n("sensitivity")["certain"], n("sensitivity")["uncertain"]) == (sens["certain"], sens["uncertain"])
    assert n("acceptance")["points"] > 0 and "cameras_without_target_point" in n("acceptance")
    # the panels' own payloads come back, and the results are in the session as if the panels had run
    assert res["results"]["coverage"]["band_share"] == cov["band_share"] and "overlay" in res["results"]["coverage"]
    e = session.SESSION.get(tid)
    assert all(k in e.results for k in ("multi", "mixed", "night", "approach", "network", "sensitivity"))
    assert res["counts"] == {"ok": 8, "skipped": 2, "failed": 0}


def test_optional_steps_skip_with_turkish_reasons():
    tid = make(ptz=False)
    res = ok("analysis.full", terrain_id=tid, steps={"coverage": {"max_px": 300}})
    st = by_id(res)
    assert st["mixed"]["reason"] == "plan PTZ içermiyor"
    assert st["night"]["reason"] == "gece tarihi seçilmedi"
    assert "yaklaşma" in st["approach"]["reason"] and "hassasiyet" in st["sensitivity"]["reason"]
    assert st["network"]["status"] == "ok"                    # network defaults on
    assert all(s["status"] != "failed" for s in res["steps"])
    off = by_id(ok("analysis.full", terrain_id=tid, steps={"coverage": {"max_px": 300}, "network": False}))
    assert off["network"]["status"] == "skipped"


def test_a_failing_step_does_not_stop_the_others():
    tid = make()
    res = ok("analysis.full", terrain_id=tid, steps={**STEPS, "night": {**NIGHT, "datetime": "not-a-date"}})
    st = by_id(res)
    assert st["night"]["status"] == "failed" and "datetime" in st["night"]["error"]
    for k in ("coverage", "mixed", "approach", "network", "sensitivity", "statement", "acceptance"):
        assert st[k]["status"] == "ok", (k, st[k])
    assert res["counts"]["failed"] == 1


def test_output_folder_writes_every_file_the_zip_and_the_summary(tmp_path):
    tid = make()
    out = tmp_path / "cikti"
    res = ok("analysis.full", terrain_id=tid, steps=STEPS, out_dir=str(out), stem="saha", project_name="Deneme", target_height_m=1.8)
    st = by_id(res)
    assert all(s["status"] == "ok" for s in res["steps"]), [(s["id"], s.get("error")) for s in res["steps"]]
    names = {p.name for p in out.iterdir()}
    for want in ("saha-plan-beyani.md", "saha-kabul-foyu.csv", "saha-kabul-foyu.pdf", "saha-gorusalani.csv",
                 "saha-gorusalani.pdf", "saha-teklif.zip", "analiz-ozeti.md"):
        assert want in names, (want, sorted(names))
    assert {Path(p).name for p in res["written"]} >= {"saha-teklif.zip", "analiz-ozeti.md"}
    assert (out / "saha-plan-beyani.md").read_text(encoding="utf-8").startswith("# EN 62676-4")
    with zipfile.ZipFile(out / "saha-teklif.zip") as z:
        zn = z.namelist()
    assert len(zn) >= 4
    md = (out / "analiz-ozeti.md").read_text(encoding="utf-8")
    for s in res["steps"]:
        assert s["title"] in md
    for word in ("Varsayımlar", "TEMSİLİ", "Hedef boyu", "inceltme", "Veri hassasiyeti varsayımları"):
        assert word in md, word
    assert st["package"]["files"] == [str(out / "saha-teklif.zip")]


def test_cancel_stops_between_steps_and_keeps_finished_results():
    tid = make(ptz=False)
    token = CancelToken()

    def notify(_m, p):
        if "Veri hassasiyeti" in p.get("message", ""):          # the run reached the sensitivity step
            token.cancel()

    with pytest.raises(Cancelled):
        analysis_full({"terrain_id": tid, "steps": {"coverage": {"max_px": 300}}}, Context(1, token, notify))
    e = session.SESSION.get(tid)
    assert "multi" in e.results                       # coverage finished before the cancel
    assert "cost" in e.results                         # the network step before it finished
    assert "sensitivity" not in e.results              # cancelled inside the sensitivity runs


def test_an_old_project_without_the_new_settings_runs():
    """No ``steps`` at all: coverage + network + statement + acceptance run, the optional panels are skipped."""
    tid = make(ptz=False)
    res = ok("analysis.full", terrain_id=tid)
    st = by_id(res)
    assert st["coverage"]["status"] == st["network"]["status"] == st["statement"]["status"] == st["acceptance"]["status"] == "ok"
    assert st["night"]["status"] == "skipped"


def test_needs_a_plan_and_valid_steps():
    session.reset()
    tid = ok("terrain.procedural", preset="ridge_and_valley", grid=60, width_m=800.0)["terrain_id"]
    assert "plan" in call("analysis.full", terrain_id=tid)["error"]["message"]
    tid = make(ptz=False)
    assert call("analysis.full", terrain_id=tid, steps=[1])["error"]["code"] == -32602
    assert call("analysis.full", terrain_id=tid, steps={"night": 5})["error"]["code"] == -32602


def test_analysis_settings_pass_through_project_io(tmp_path):
    """The desktop keeps the panels' settings in terrain_extra.analysis_settings (and the Optics lab's ANPR in
    terrain_extra.anpr): a headless load + save must keep them verbatim, and an old file without them loads."""
    from cctv_simulator import project_io

    settings = {"night": {"when": "2026-01-11T23:00", "ir": None, "ir_on": False},
                "ptz": {"mode": "tour", "presets": [{"name": "A", "pan_deg": 1, "tilt_deg": -2, "dwell_s": 3}]},
                "full": {"include": {"night": True}, "out_dir": "C:/cikti"}}
    anpr = {"setup": {"lanes": 3, "aim_x_m": None}, "focal": 9.5}
    src = tmp_path / "p.json"
    src.write_text(json.dumps({"cameras": [dataclasses.asdict(CameraConfig(name="K1"))],
                               "terrain": {"source": "procedural", "fence_points": [[1, 2], [3, 4]],
                                           "analysis_settings": settings, "anpr": anpr}}), encoding="utf-8")
    proj = project_io.load_project(src)
    assert proj.terrain_extra["analysis_settings"] == settings and proj.terrain_extra["anpr"] == anpr
    out = tmp_path / "again.json"
    project_io.save_project(out, proj)
    terr = json.loads(out.read_text(encoding="utf-8"))["terrain"]
    assert terr["analysis_settings"] == settings and terr["anpr"] == anpr and terr["fence_points"] == [[1, 2], [3, 4]]
    old = tmp_path / "old.json"
    old.write_text(json.dumps({"cameras": [dataclasses.asdict(CameraConfig(name="K1"))]}), encoding="utf-8")
    assert "analysis_settings" not in project_io.load_project(old).terrain_extra
