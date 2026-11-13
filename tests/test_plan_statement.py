"""EN 62676-4 conformity statement of a plan: zone x level x result."""
from __future__ import annotations

import dataclasses
import io
import json
import zipfile
from types import SimpleNamespace

import numpy as np
import pytest

from cctv_simulator.models import CameraConfig
from cctv_simulator.plan_statement import (VERDICT_FAIL, VERDICT_OK, VERDICT_PARTIAL, build_plan_statement,
                                           declared_level, plan_conformity)
from cctv_simulator.service import session
from cctv_simulator.service.methods import METHODS
from cctv_simulator.service.protocol import APP_ERROR, Server
from cctv_simulator.viewshed_3d import ZONE_DETECT, ZONE_IDENT, ZONE_OBSERVE, ZONE_RECOG

FIX = CameraConfig(name="Sabit", sensor_name='1/2.8"', resolution_name="4 MP (2K - 2688x1520)",
                   focal_min_mm=2.8, focal_max_mm=12.0)
PTZ = CameraConfig(name="PTZ", focal_min_mm=4.8, focal_max_mm=153.0, resolution_name="2 MP (1080p - 1920x1080)")
THERMAL = CameraConfig(name="Termal", sensor_name="LWIR 640x512 (12µm)", resolution_name="LWIR (640x512)",
                       focal_min_mm=25.0, focal_max_mm=225.0)
SQUARE = [[300, 300], [700, 300], [700, 700], [300, 700]]


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


@pytest.fixture
def hills():
    session.reset()
    return ok("terrain.procedural", preset="ridge_and_valley", grid=100, width_m=1000.0)["terrain_id"]


def _plan(target_ppm=40.0, ptz=0):
    return SimpleNamespace(target_ppm=target_ppm, pole_count=4, placed_cameras=[1, 2, 3, 4], ptz_cameras=[1] * ptz)


def test_declared_level_is_the_highest_the_target_reaches():
    assert declared_level(FIX, 40.0) == ZONE_DETECT          # 40 px/m holds Detection (25), not Observation (62,5)
    assert declared_level(FIX, 62.5) == ZONE_OBSERVE
    assert declared_level(FIX, 250.0) == ZONE_IDENT
    assert declared_level(FIX, 10.0) == ZONE_DETECT          # below every level: the lowest is what is judged
    assert declared_level(FIX, 40.0, area_level=ZONE_RECOG) == ZONE_RECOG
    assert declared_level(THERMAL, 250.0) == ZONE_RECOG      # a thermal has no fourth level


def test_rows_are_counted_from_the_engine_grids_and_judged_against_the_goal():
    dori = np.zeros((10, 10), np.int8)
    dori[:, :9] = ZONE_DETECT            # 90 % detection ...
    dori[:, :5] = ZONE_RECOG             # ... half of it recognition
    ptz = np.zeros((10, 10), np.int8)
    ptz[:, 9] = ZONE_DETECT              # the last column only by a patrol
    mv = SimpleNamespace(dori_grid=dori)
    zone = [{"name": "Bant", "mask": np.ones((10, 10), bool), "kind": "area", "size": 1e4}]

    c = plan_conformity(_plan(), FIX, mv, zone, cell_size_m=10.0, ptz_zone=ptz, goal_frac=0.95)
    rows = {r["level"]: r for r in c["zones"][0]["rows"]}
    assert rows["detect"]["continuous"] == pytest.approx(0.9) and rows["detect"]["patrol"] == pytest.approx(0.1)
    assert rows["detect"]["verdict"] == VERDICT_PARTIAL and rows["detect"]["declared"]
    assert rows["observe"]["continuous"] == rows["recognize"]["continuous"] == pytest.approx(0.5)
    assert rows["identify"]["verdict"] == VERDICT_FAIL
    assert c["verdict"] == VERDICT_PARTIAL                    # only the declared level decides

    assert plan_conformity(_plan(), FIX, mv, zone, cell_size_m=10.0, goal_frac=0.95)["verdict"] == VERDICT_FAIL
    assert plan_conformity(_plan(), FIX, mv, zone, cell_size_m=10.0, goal_frac=0.9)["verdict"] == VERDICT_OK
    # a plan built for Recognition is judged at Recognition
    assert plan_conformity(_plan(125.0), FIX, mv, zone, cell_size_m=10.0, goal_frac=0.9)["verdict"] == VERDICT_FAIL


def test_statement_says_what_it_rests_on():
    mv = SimpleNamespace(dori_grid=np.full((4, 4), ZONE_DETECT, np.int8))
    zone = [{"name": "Çit bandı (±40 m)", "mask": np.ones((4, 4), bool), "kind": "area", "size": 1600.0}]
    conf = plan_conformity(_plan(), FIX, mv, zone, cell_size_m=10.0)
    md = build_plan_statement(conf, plan=_plan(), camera=FIX, conditions=["Hava: Berrak hava"], project_name="Tesis",
                              measured=False)
    assert "## Sonuç: UYGUN\n" in md and "Algılama (beyan)" in md and "EN 62676-4 §6.2" in md
    assert "BAĞLAYICI DEĞİL" in md and "Hava: Berrak hava" in md and "Tesis" in md
    assert "Aralıklı" not in md                               # no PTZ: no patrol column
    assert "BAĞLAYICI DEĞİL" not in build_plan_statement(conf, plan=_plan(), camera=FIX, conditions=[], measured=True)


def test_thermal_statement_uses_johnson_and_three_levels():
    mv = SimpleNamespace(dori_grid=np.full((4, 4), ZONE_RECOG, np.int8))
    zone = [{"name": "Bant", "mask": np.ones((4, 4), bool), "kind": "area", "size": 1600.0}]
    conf = plan_conformity(_plan(1.3), THERMAL, mv, zone, cell_size_m=10.0)
    assert [r["level"] for r in conf["zones"][0]["rows"]] == ["detect", "observe", "recognize"]
    assert conf["zones"][0]["rows"][0]["required_ppm"] == pytest.approx(1.3) and "Johnson" in conf["clause"]


def test_service_statement_is_the_coverage_reports_own_numbers(hills, tmp_path):
    cam = dataclasses.asdict(FIX)
    ok("perimeter.plan", terrain_id=hills, camera=cam, fence=SQUARE, target_ppm=40, mast_m=6)
    r = call("export.plan_statement", terrain_id=hills)
    assert r["error"]["code"] == APP_ERROR and "Birleşik Kapsama" in r["error"]["message"]

    cov = ok("perimeter.coverage", terrain_id=hills, level="detect", max_px=300)
    s = ok("export.plan_statement", terrain_id=hills, project_name="Tesis", path=str(tmp_path / "beyan.md"))
    band, line = s["zones"]
    assert band["kind"] == "area" and band["size"] == pytest.approx(cov["band_area_m2"])
    for row in band["rows"]:
        assert row["continuous"] == pytest.approx(cov["band_share"][row["level"]])
    assert line["name"] == "Çit çizgisi" and line["rows"][0]["continuous"] == pytest.approx(cov["fence_line"]["detect"])
    assert s["declared_level"] == "detect" and s["measured"] is False
    text = (tmp_path / "beyan.md").read_text(encoding="utf-8")
    assert text == s["markdown"] and "BAĞLAYICI DEĞİL" in text
    ok("export.plan_statement", terrain_id=hills, path=str(tmp_path / "beyan.pdf"))
    assert (tmp_path / "beyan.pdf").read_bytes().startswith(b"%PDF-")

    # a new plan drops the masks: the statement cannot pair plan B with plan A's coverage
    ok("perimeter.plan", terrain_id=hills, camera=cam, fence=SQUARE, target_ppm=40, mast_m=8)
    assert "error" in call("export.plan_statement", terrain_id=hills)


def test_patrol_column_and_border_zone(hills):
    cam, ptz = dataclasses.asdict(FIX), dataclasses.asdict(PTZ)
    ok("perimeter.plan", terrain_id=hills, camera=cam, fence=[[200, 500], [800, 500]], closed=False,
       line_mode="border", watch_side="left", target_ppm=40, mast_m=8, ptz_camera=ptz, ptz_every_n=2)
    cov = ok("perimeter.coverage", terrain_id=hills, band_m=150, max_px=300)
    s = ok("export.plan_statement", terrain_id=hills)
    assert len(s["zones"]) == 1 and "İzlenen şerit" in s["zones"][0]["name"]      # no fence-line verdict on a border
    assert s["has_patrol"] and "Aralıklı (PTZ devriyesi)" in s["markdown"]
    for row in s["zones"][0]["rows"]:
        assert row["patrol"] == pytest.approx(cov["band_share_ptz"][row["level"]])


def test_offer_package_carries_the_plan_statement_once_coverage_ran(hills, tmp_path):
    ok("perimeter.plan", terrain_id=hills, camera=dataclasses.asdict(FIX), fence=SQUARE, target_ppm=40, mast_m=6)
    r = ok("export.package", terrain_id=hills, path=str(tmp_path / "a.zip"), project_name="Tesis")
    assert any("plan uygunluk beyanı" in s for s in r["skipped"])
    ok("perimeter.coverage", terrain_id=hills, max_px=300)
    r = ok("export.package", terrain_id=hills, path=str(tmp_path / "b.zip"), project_name="Tesis")
    with zipfile.ZipFile(tmp_path / "b.zip") as z:
        names = set(z.namelist())
        md = z.read("05-plan-uygunluk-beyani.md").decode("utf-8")
    assert {"05-plan-uygunluk-beyani.md", "05-plan-uygunluk-beyani.pdf"} <= names
    assert "PLAN UYGUNLUK BEYANI" in md and "Tesis" in md
    assert not any("plan uygunluk" in s for s in r["skipped"])


def test_verdict_heading_is_upper_cased_in_turkish():
    mv = SimpleNamespace(dori_grid=np.zeros((4, 4), np.int8))
    zone = [{"name": "Bant", "mask": np.ones((4, 4), bool), "kind": "area", "size": 1600.0}]
    conf = plan_conformity(_plan(), FIX, mv, zone, cell_size_m=10.0)
    assert "## Sonuç: UYGUN DEĞİL" in build_plan_statement(conf, plan=_plan(), camera=FIX, conditions=[])
