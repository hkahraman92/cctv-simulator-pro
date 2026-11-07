"""Core service: optics bench verdict, image-quality measurement, 3D frame."""
from __future__ import annotations

import dataclasses
import io
import json

import numpy as np
import pytest
from PIL import Image

from cctv_simulator.calculations import calculate_for_camera
from cctv_simulator.cctv_iq import synthetic_edge
from cctv_simulator.models import CameraConfig
from cctv_simulator.optics_bench import evaluate_requirement
from cctv_simulator.perspective_3d import Perspective3DEngine
from cctv_simulator.service.methods import METHODS
from cctv_simulator.service.protocol import Server


def ok(method, **params):
    out = io.StringIO()
    server = Server(METHODS, out=out)
    server.handle_line(json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}))
    server.wait_idle()
    reply = next(json.loads(x) for x in out.getvalue().splitlines() if json.loads(x).get("id") == 1)
    assert "result" in reply, reply.get("error")
    return reply["result"]


@pytest.mark.parametrize("dist,weather", [(5.0, ""), (25.0, ""), (40.0, "Sis"), (2.0, "")])
def test_bench_verdict_is_the_shared_one(dist, weather):
    cam = CameraConfig(focal_min_mm=2.8, focal_max_mm=12.0, tilt_deg=12.0, pole_height_m=4.0, ir_range_m=30)
    r = ok("optics.bench", camera=dataclasses.asdict(cam), mode="max", req_ppm=125, distance_m=dist, weather=weather)
    want = evaluate_requirement(cam, calculate_for_camera(cam, "max", []), 125, dist, weather)
    assert r["achieved_ppm"] == want[0]
    assert r["reasons"] == want[2] and r["pass"] is (not want[2])
    assert (r["atmospheric_limit_m"] is None) == (want[1] == float("inf"))


def test_bench_names_why_it_fails():
    cam = dataclasses.asdict(CameraConfig(focal_min_mm=2.8, focal_max_mm=2.8, tilt_deg=30.0, ir_range_m=10))
    r = ok("optics.bench", camera=cam, req_ppm=250, distance_m=60)
    assert not r["pass"]
    assert "piksel yoğunluğu yetersiz" in r["reasons"] and "IR menzili dışında" in r["reasons"]
    assert r["dori_reach_m"]["identify"] < r["dori_reach_m"]["detect"]


def test_project_overlap_matches_the_engine():
    """Two cameras back to back: each dead zone is covered by the other (the
    classic plan's "kör nokta kapatılıyor" rule), and the check point verdict."""
    from cctv_simulator.calculations import analyze_dead_zone_coverage, target_analysis_for_result
    from cctv_simulator.models import DEFAULT_LEVELS, TargetPoint

    a = CameraConfig(name="A", pos_x_m=0, pos_y_m=0, heading_deg=0, tilt_deg=20, pole_height_m=5)
    b = CameraConfig(name="B", pos_x_m=6, pos_y_m=0, heading_deg=180, tilt_deg=20, pole_height_m=5)
    r = ok("optics.project", cameras=[dataclasses.asdict(a), dataclasses.asdict(b)], target={"x_m": 4, "y_m": 0.5})
    res = {c.name: [calculate_for_camera(c, "min", DEFAULT_LEVELS, with_recommendations=False)] for c in (a, b)}
    analyze_dead_zone_coverage(res, [a, b])
    for cam in r["cameras"]:
        assert cam["dead_zone_covered_by"] == res[cam["name"]][0].dead_zone_covered_by
        want = target_analysis_for_result(TargetPoint(x_m=4, y_m=0.5), res[cam["name"]][0], DEFAULT_LEVELS[11])
        assert cam["target"]["ppm"] == want["ppm"] and cam["target"]["status"] == want["status"]
    assert r["cameras"][0]["dead_zone_covered_by"] == ["B"]


def test_custom_level_drives_distances_and_the_check_point():
    from cctv_simulator.models import DEFAULT_LEVELS

    custom = [dataclasses.asdict(lv) for lv in DEFAULT_LEVELS] + [
        {"key": "Firma_Yuz", "name": "Firma: Yüz (400 px/m)", "ppm": 400, "color": "#ffffff", "level_type": "Özel"}]
    cam = dataclasses.asdict(CameraConfig(name="A", heading_deg=0, tilt_deg=10))
    r = ok("optics.project", cameras=[cam], levels=custom, target={"x_m": 3, "y_m": 0}, level_key="Firma_Yuz")
    c = r["cameras"][0]
    assert "Firma_Yuz" in c["ground_distances"]
    assert c["ground_distances"]["Firma_Yuz"] < c["ground_distances"]["Characterize"]
    assert r["level"]["ppm"] == 400 and c["target"]["ppm_ok"] == (c["target"]["ppm"] >= 400)


def test_plan_image_reads_a_picture(tmp_path):
    p = tmp_path / "plan.png"
    Image.new("RGB", (800, 400), "white").save(p)
    r = ok("plan.image", path=str(p), max_px=400)
    assert r["source_width_px"] == 800 and r["aspect"] == 0.5 and r["width"] == 400


def test_iq_measures_a_known_edge(tmp_path):
    p = tmp_path / "edge.png"
    Image.fromarray(synthetic_edge(160, blur_px=1.5).astype(np.uint8)).save(p)
    r = ok("iq.measure", path=str(p), nominal_mp=8)
    assert 0.05 < r["k"] <= 1.0
    assert r["effective_mp"] == pytest.approx(8 * r["k"] ** 2)


def test_scene_frame_uses_the_perspective_engine():
    cam = CameraConfig(focal_min_mm=2.8, focal_max_mm=12.0, pole_height_m=4.0, tilt_deg=10.0)
    r = ok("scene.render", camera=dataclasses.asdict(cam), focal_mm=6.0, width=640, height=360,
           target="human", target_dist_m=20.0)
    eng = Perspective3DEngine(cam, focal_mm=6.0, viewport_size=(640, 360))
    assert r["ppm"] == eng.calculate_ppm_at_distance(20.0, target_h_m=1.8)
    assert r["frame"]["data"].startswith("data:image/jpeg") and r["frame"]["width"] == 640
    assert r["grid"] and r["target_head"]["visible"]
    fog = ok("scene.render", camera=dataclasses.asdict(cam), focal_mm=6.0, width=320, height=180,
             target_dist_m=200.0, weather="Yoğun sis")
    assert fog["transmittance"] < r["transmittance"]
