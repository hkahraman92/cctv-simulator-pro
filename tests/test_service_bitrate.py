"""bitrate.estimate service: parity with the engine, totals, overrides, errors."""
from __future__ import annotations

import dataclasses
import io
import json

from cctv_simulator.bitrate import estimate
from cctv_simulator.database import load_camera_library
from cctv_simulator.models import CameraConfig
from cctv_simulator.service.methods import METHODS
from cctv_simulator.service.protocol import APP_ERROR, INVALID_PARAMS, Server

CAM = dataclasses.asdict(CameraConfig(name="K1"))


def call(**params):
    out = io.StringIO()
    server = Server(METHODS, out=out)
    server.handle_line(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "bitrate.estimate", "params": params}))
    server.wait_idle()
    return json.loads(out.getvalue().splitlines()[-1])


def test_row_equals_the_engine_and_totals_are_sums():
    r = call(cameras=[{"camera": CAM}, {"camera": CAM, "label": "B"}], defaults={"codec": "h264", "fps": 15},
             retention_days=10, raid_overhead=0.1)["result"]
    want = estimate(CameraConfig(name="K1"), codec="h264", fps=15, retention_days=10, raid_overhead=0.1)
    assert r["rows"][0] == {"label": "K1", "model": CAM["model_name"], **dataclasses.asdict(want)}
    assert r["rows"][1]["label"] == "B"
    t = r["totals"]
    assert t["avg_record_mbps"] == sum(x["avg_record_mbps"] for x in r["rows"])
    assert t["peak_mbps"] == sum(x["peak_mbps"] for x in r["rows"])
    assert t["storage_tb"] == sum(x["storage_tb"] for x in r["rows"])
    assert r["assumptions"] and any("tahmin" in a for a in r["assumptions"])


def test_per_camera_option_beats_defaults():
    r = call(cameras=[{"camera": CAM}, {"camera": CAM, "codec": "mjpeg"}], defaults={"codec": "h265"})["result"]
    assert r["rows"][0]["codec"] == "h265" and r["rows"][1]["codec"] == "mjpeg"


def test_library_model_row_uses_its_record():
    name = next(n for n, m in load_camera_library().items() if isinstance(m, dict) and m)
    row = call(cameras=[{"model": name}])["result"]["rows"][0]
    assert row["model"] == name and row["day_mbps"] > 0


def test_errors_are_turkish_and_typed():
    e = call(cameras=[{"model": "Yok Böyle Model"}])["error"]
    assert e["code"] == APP_ERROR and "Kütüphanede model yok" in e["message"]
    assert call(cameras=[])["error"]["code"] == INVALID_PARAMS
    assert call(cameras=[{"camera": CAM, "codec": "xyz"}])["error"]["code"] == INVALID_PARAMS
    assert call(cameras=[{"camera": CAM}], retention_days="x")["error"]["code"] == INVALID_PARAMS
    assert call()["error"]["code"] == INVALID_PARAMS
