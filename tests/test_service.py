"""Core service: JSON-RPC framing, cancellation, and engine parity."""
from __future__ import annotations

import dataclasses
import io
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from cctv_simulator.calculations import calculate_for_camera
from cctv_simulator.models import DEFAULT_LEVELS, CameraConfig
from cctv_simulator.service import API_VERSION
from cctv_simulator.service.methods import METHODS
from cctv_simulator.service.protocol import (
    INVALID_PARAMS, INVALID_REQUEST, METHOD_NOT_FOUND, PARSE_ERROR, REQUEST_CANCELLED, Server,
)

ROOT = Path(__file__).resolve().parents[1]


def _run(lines, methods=METHODS):
    out = io.StringIO()
    server = Server(methods, out=out)
    for line in lines:
        server.handle_line(line if isinstance(line, str) else json.dumps(line))
    server.wait_idle()
    msgs = [json.loads(x) for x in out.getvalue().splitlines()]
    return {m.get("id"): m for m in msgs if "id" in m}, [m for m in msgs if "id" not in m]


def _req(i, method, **params):
    return {"jsonrpc": "2.0", "id": i, "method": method, "params": params}


def test_framing_errors_are_json_rpc_errors():
    by_id, _ = _run(["{not json", {"jsonrpc": "1.0", "id": 2, "method": "x"},
                     _req(3, "nope.missing"), {"jsonrpc": "2.0", "id": 4, "method": "system.ping", "params": [1]}])
    assert by_id[None]["error"]["code"] == PARSE_ERROR
    assert by_id[2]["error"]["code"] == INVALID_REQUEST
    assert by_id[3]["error"]["code"] == METHOD_NOT_FOUND
    assert by_id[4]["error"]["code"] == INVALID_PARAMS


def test_notifications_get_no_reply():
    by_id, notes = _run([{"jsonrpc": "2.0", "method": "system.ping"}])
    assert by_id == {} and notes == []


def test_info_and_describe_list_every_method():
    by_id, _ = _run([_req(1, "system.info"), _req(2, "system.describe")])
    assert by_id[1]["result"]["api_version"] == API_VERSION
    names = {m["name"] for m in by_id[2]["result"]["methods"]}
    assert names == set(METHODS)
    assert all(m["summary"] for m in by_id[2]["result"]["methods"])


@pytest.mark.parametrize("mode", ["min", "max"])
def test_optics_matches_the_engine_exactly(mode):
    cam = CameraConfig(focal_min_mm=2.8, focal_max_mm=12.0, tilt_deg=12.0, pole_height_m=5.0)
    want = calculate_for_camera(cam, mode, DEFAULT_LEVELS)
    by_id, _ = _run([_req(1, "optics.calculate", camera=dataclasses.asdict(cam), mode=mode)])
    got = by_id[1]["result"]
    assert got["focal_mm"] == want.focal_mm
    assert got["hfov_deg"] == want.hfov_deg and got["dead_zone_m"] == want.dead_zone_m
    for key, dist in want.ground_distances.items():
        assert got["ground_distances"][key] == (dist if dist != float("inf") else None)
    assert [r["ground_distance_m"] for r in got["rows"]] == [r.ground_distance_m for r in want.rows]


def test_focal_override_evaluates_inside_the_lens_range():
    cam = dataclasses.asdict(CameraConfig(focal_min_mm=2.8, focal_max_mm=12.0))
    by_id, _ = _run([_req(1, "optics.calculate", camera=cam, focal_mm=6.0),
                     _req(2, "optics.calculate", camera=cam, focal_mm=20.0)])
    assert by_id[1]["result"]["focal_mm"] == 6.0
    assert by_id[1]["result"]["optics"]["focal_mm"] == 6.0
    assert by_id[2]["error"]["code"] == INVALID_PARAMS


def test_bad_camera_fields_are_rejected_by_name():
    by_id, _ = _run([_req(1, "optics.calculate", camera={"focal_min_mm": "x"}),
                     _req(2, "optics.calculate", camera={"lens": 4}),
                     _req(3, "optics.calculate", camera={"sensor_name": "yok"})])
    assert "focal_min_mm" in by_id[1]["error"]["message"]
    assert by_id[2]["error"]["data"]["unknown"] == ["lens"]
    assert "sensör" in by_id[3]["error"]["message"]


def test_infinite_geometry_is_null_not_invalid_json():
    cam = dataclasses.asdict(CameraConfig(tilt_deg=0.0))
    by_id, _ = _run([_req(1, "optics.calculate", camera=cam)])
    assert by_id[1]["result"]["max_geom_dist_m"] is None


def test_library_list_and_get():
    by_id, _ = _run([_req(1, "library.list")])
    models = by_id[1]["result"]["models"]
    assert by_id[1]["result"]["count"] == len(models) > 10
    name = next(m["name"] for m in models if m["is_ptz"])
    by_id, _ = _run([_req(2, "library.get", name=name), _req(3, "library.get", name="yok")])
    assert by_id[2]["result"]["summary"]["name"] == name
    assert by_id[3]["error"]["code"] == -32000


def test_every_library_model_becomes_a_valid_engine_camera():
    by_id, _ = _run([_req(1, "library.list")])
    names = [m["name"] for m in by_id[1]["result"]["models"]]
    base = dataclasses.asdict(CameraConfig(pole_height_m=9.0, tilt_deg=4.0))
    by_id, _ = _run([_req(i, "library.camera", name=n, base=base) for i, n in enumerate(names)])
    for i, name in enumerate(names):
        cam = by_id[i]["result"]["camera"]
        assert cam["model_name"] == name
        assert cam["pole_height_m"] == 9.0 and cam["tilt_deg"] == 4.0   # site kept
    t10 = next(i for i, n in enumerate(names) if "T10" in n)
    assert by_id[t10]["result"]["camera"]["focal_max_mm"] == 350.0
    assert by_id[t10]["result"]["is_ptz"] is True


def test_project_roundtrip(tmp_path):
    cam = dataclasses.asdict(CameraConfig(name="Giriş", pos_x_m=3.0))
    path = str(tmp_path / "p.json")
    by_id, _ = _run([_req(1, "project.save", path=path, project={"cameras": [cam], "project_name": "Deneme"})])
    assert "result" in by_id[1], by_id[1]
    by_id, _ = _run([_req(2, "project.load", path=path)])
    proj = by_id[2]["result"]["project"]
    assert proj["project_name"] == "Deneme" and proj["cameras"][0]["name"] == "Giriş"


def test_terrain_block_extras_survive_a_load_and_save(tmp_path):
    """The map window's GUI-only keys (fence, line mode, mixed PTZ) used to be
    dropped by any headless re-save."""
    src = tmp_path / "classic.json"
    cam = dataclasses.asdict(CameraConfig(name="K1"))
    src.write_text(json.dumps({"cameras": [cam], "terrain": {
        "source": "region", "region_id": "kizilay-20261107", "width_m": 1000, "weather": "Pus",
        "fence_points": [[1, 2], [3, 4], [5, 6]], "fence_closed": True, "line_mode": "border",
        "mixed_ptz": {"camera": "PTZ", "every_n": 3, "patrol_span_deg": 180}}}), encoding="utf-8")
    by_id, _ = _run([_req(1, "project.load", path=str(src))])
    proj = by_id[1]["result"]["project"]
    assert proj["terrain_region_id"] == "kizilay-20261107"
    assert proj["terrain_extra"]["fence_points"] == [[1, 2], [3, 4], [5, 6]]
    out = tmp_path / "again.json"
    by_id, _ = _run([_req(2, "project.save", path=str(out), project=proj)])
    assert "result" in by_id[2], by_id[2]
    terr = json.loads(out.read_text(encoding="utf-8"))["terrain"]
    assert terr["line_mode"] == "border" and terr["mixed_ptz"]["every_n"] == 3 and terr["source"] == "region"


def test_cancel_and_progress():
    started = threading.Event()

    def slow(params, ctx):
        for i in range(200):
            ctx.progress(i, 200, "çalışıyor")
            started.set()
            time.sleep(0.01)
        return {"done": True}

    out = io.StringIO()
    server = Server({"slow": slow, **METHODS}, out=out)
    server.handle_line(json.dumps(_req(7, "slow")))
    assert started.wait(2)
    server.handle_line(json.dumps(_req(8, "system.ping")))      # not blocked by the slow job
    server.handle_line(json.dumps({"jsonrpc": "2.0", "method": "$/cancelRequest", "params": {"id": 7}}))
    server.wait_idle()
    msgs = [json.loads(x) for x in out.getvalue().splitlines()]
    replies = {m["id"]: m for m in msgs if "id" in m}
    assert replies[7]["error"]["code"] == REQUEST_CANCELLED
    assert replies[8]["result"] == {"pong": True}
    assert any(m.get("method") == "$/progress" and m["params"]["id"] == 7 for m in msgs)


def test_every_engine_module_imports_without_tk():
    """cctv-core.exe is built without Tk: any engine module importing tkinter
    at top level would crash the frozen core the first time it is used
    (compliance.py and exporters.py did)."""
    code = (
        "import sys, pkgutil, importlib\n"
        "sys.modules['tkinter'] = None; sys.modules['_tkinter'] = None\n"
        "import cctv_simulator\n"
        "bad = []\n"
        "for m in pkgutil.walk_packages(cctv_simulator.__path__, 'cctv_simulator.'):\n"
        "    n = m.name\n"
        "    if '.ui' in n or n.endswith(('.theme', '.__main__')):\n"
        "        continue\n"
        "    try:\n"
        "        importlib.import_module(n)\n"
        "    except Exception as e:\n"
        "        bad.append(f'{n}: {e}')\n"
        "print('\\n'.join(bad))\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "", proc.stdout


def test_stdio_process_keeps_stdout_clean():
    """The real sidecar: stdout carries only frames even if the engine prints."""
    lines = [json.dumps(_req(1, "system.info")),
             json.dumps(_req(2, "optics.calculate", camera=dataclasses.asdict(CameraConfig())))]
    proc = subprocess.run([sys.executable, "-m", "cctv_simulator.service"], cwd=ROOT,
                          input="\n".join(lines) + "\n", capture_output=True, text=True,
                          encoding="utf-8", timeout=60)
    assert proc.returncode == 0, proc.stderr
    frames = [json.loads(x) for x in proc.stdout.splitlines()]
    assert {f["id"] for f in frames} == {1, 2}
    assert "hazır" in proc.stderr


def test_sidecar_starts_on_a_console_without_turkish_letters():
    """A cp1252 console (the CI runner) cannot encode the dotless i of the
    ready line; the sidecar must switch stderr to UTF-8 before writing it."""
    env = {**os.environ, "PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0"}
    proc = subprocess.run([sys.executable, "-m", "cctv_simulator.service"], cwd=ROOT,
                          input=(json.dumps(_req(1, "system.info")) + "\n").encode("utf-8"), capture_output=True,
                          env=env, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert "hazır" in proc.stderr.decode("utf-8")
    assert json.loads(proc.stdout.decode("utf-8").splitlines()[0])["id"] == 1
