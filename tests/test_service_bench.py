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
