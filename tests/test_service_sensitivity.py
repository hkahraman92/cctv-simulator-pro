"""perimeter.sensitivity through the core service: parity with perimeter.coverage, reports, validation."""
from __future__ import annotations

import base64
import dataclasses
import io
import json

import numpy as np
import pytest
from PIL import Image

from cctv_simulator import surface_obstacles as obs
from cctv_simulator.models import CameraConfig
from cctv_simulator.service import session
from cctv_simulator.service.methods import METHODS
from cctv_simulator.service.protocol import APP_ERROR, INVALID_PARAMS, REQUEST_CANCELLED, Server
from cctv_simulator.terrain_loader import TerrainData

CAM = dataclasses.asdict(CameraConfig(sensor_name='1/2.8"', resolution_name="4 MP (2K - 2688x1520)",
                                      focal_min_mm=2.8, focal_max_mm=12.0))
FENCE = [[300, 300], [900, 300], [900, 900], [300, 900]]


def call(method, **params):
    out = io.StringIO()
    server = Server(METHODS, out=out)
    server.handle_line(json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}))
    server.wait_idle()
    msgs = [json.loads(x) for x in out.getvalue().splitlines()]
    return next(m for m in msgs if m.get("id") == 1), [m for m in msgs if m.get("method") == "$/progress"]


def ok(method, **params):
    reply, _ = call(method, **params)
    assert "result" in reply, reply.get("error")
    return reply["result"]


@pytest.fixture(autouse=True)
def fresh_session():
    session.reset()
    yield


@pytest.fixture
def planned():
    tid = ok("terrain.procedural", preset="ridge_and_valley", grid=60, width_m=1200.0)["terrain_id"]
    ok("perimeter.plan", terrain_id=tid, camera=CAM, fence=FENCE, target_ppm=40, mast_m=6)
    return tid


def _picture(img):
    return Image.open(io.BytesIO(base64.b64decode(img["data"].split(",", 1)[1])))


def test_terrain_info_carries_the_documented_default_error(planned):
    t = ok("terrain.procedural", preset="canyon", grid=50, width_m=800.0)
    d = t["sensitivity_defaults"]
    assert d["dem_sigma_m"] > 0 and d["dem_corr_m"] > 0 and "ölçüm değil" in d["basis"]       # procedural: flagged


def test_no_uncertainty_equals_the_coverage_report_exactly(planned):
    cov = ok("perimeter.coverage", terrain_id=planned, max_px=300)
    for level in ("detect", "observe"):
        r = ok("perimeter.sensitivity", terrain_id=planned, level=level, n_runs=4, dem_sigma_m=0.0,
               obstacle_uncertainty=0.0, max_px=300)
        assert r["nominal"] == cov["band_share"][level]                  # run 0 = the data as is, same expression
        assert r["uncertain"] == 0.0 and r["share_min"] == r["share_max"] == r["nominal"]
        assert r["certain"] + r["never"] == pytest.approx(1.0)
        assert r["levels"]["detect"]["nominal"] == cov["band_share"]["detect"]
        assert r["band_area_m2"] == pytest.approx(cov["band_area_m2"])
    plan_level = ok("perimeter.sensitivity", terrain_id=planned, n_runs=4, dem_sigma_m=0.0, obstacle_uncertainty=0.0,
                    max_px=300)                                           # level "plan": the coverage target's "held"
    assert plan_level["nominal"] == pytest.approx(cov["target"]["held"], abs=1e-12)


def test_default_run_gives_shares_range_assumptions_and_a_picture(planned):
    reply, prog = call("perimeter.sensitivity", terrain_id=planned, level="detect", n_runs=5, max_px=300)
    r = reply["result"]
    assert r["certain"] + r["uncertain"] + r["never"] == pytest.approx(1.0)
    assert r["share_min"] <= r["share_p10"] <= r["share_p90"] <= r["share_max"]
    assert r["share_min"] <= r["nominal"] <= r["share_max"] and len(r["run_shares"]) == 5
    assert r["measured"] is False and "ölçülmemiş" not in r["line"] and "ölçülmüş değil" in r["line"]
    assert r["assumptions"]["dem_sigma_m"] > 0 and r["assumptions"]["refine_factor"] == 1
    assert r["line"].startswith("Veri hassasiyeti: %") and "5 koşu" in r["line"]
    assert [row[0] for row in r["legend"]] == ["Kesin (tüm koşularda tutuyor)", "Veriye bağlı (bazı koşularda)",
                                               "Hiçbir koşuda tutmuyor"]
    img = _picture(r["overlay"])
    assert img.mode == "RGBA" and np.asarray(img)[..., 3].max() > 0
    assert len(prog) >= 5                                                   # one progress notice per run
    # the same seed repeats, another does not (when anything is uncertain)
    again = ok("perimeter.sensitivity", terrain_id=planned, level="detect", n_runs=5, max_px=300)
    assert again["run_shares"] == r["run_shares"]
    assert ok("perimeter.sensitivity", terrain_id=planned, level="detect", n_runs=5, seed=9, max_px=300)["run_shares"][0] \
        == r["run_shares"][0]


def test_user_overrides_replace_the_defaults_and_are_reported(planned):
    r = ok("perimeter.sensitivity", terrain_id=planned, n_runs=4, dem_sigma_m=1.5, dem_corr_m=40, max_px=200)
    assert (r["assumptions"]["dem_sigma_m"], r["assumptions"]["dem_corr_m"]) == (1.5, 40.0)
    assert r["assumptions"]["dem_basis"] == "Kullanıcı değeri"
    assert "DEM σ 1,5 m" in r["line"] and "korelasyon 40 m" in r["line"]


def test_the_result_is_dropped_with_the_plan_it_was_computed_for(planned):
    entry = session.SESSION.get(planned)
    ok("perimeter.sensitivity", terrain_id=planned, n_runs=4, max_px=200)
    assert "sensitivity" in entry.results and "sensitivity" in entry.PLAN_DERIVED
    ok("perimeter.plan", terrain_id=planned, camera=CAM, fence=FENCE, target_ppm=40, mast_m=6)
    assert "sensitivity" not in entry.results


def test_the_statement_and_the_report_carry_the_line_only_when_a_result_exists(planned, tmp_path):
    ok("perimeter.coverage", terrain_id=planned, max_px=200)
    ok("export.plan_statement", terrain_id=planned, path=str(tmp_path / "a.md"))
    plain = (tmp_path / "a.md").read_text(encoding="utf-8")
    assert "Veri hassasiyeti" not in plain
    ok("report.engineering", terrain_id=planned, camera=CAM, out_dir=str(tmp_path), stem="no", formats=["csv"])
    assert "VERİ HASSASİYETİ" not in (tmp_path / "no-gorusalani.csv").read_text(encoding="utf-8-sig")

    r = ok("perimeter.sensitivity", terrain_id=planned, n_runs=4, level="detect", max_px=200)
    ok("export.plan_statement", terrain_id=planned, path=str(tmp_path / "b.md"))
    text = (tmp_path / "b.md").read_text(encoding="utf-8")
    assert "## Veri hassasiyeti" in text and r["line"] in text and "| Algılama |" in text
    assert text.replace("## Veri hassasiyeti", "").count("Veri hassasiyeti") >= 1
    ok("report.engineering", terrain_id=planned, camera=CAM, out_dir=str(tmp_path), stem="yes", formats=["csv"])
    csv_text = (tmp_path / "yes-gorusalani.csv").read_text(encoding="utf-8-sig")
    assert "VERİ HASSASİYETİ" in csv_text and r["line"] in csv_text

    a = ok("scenario.snapshot", terrain_id=planned, label="A")["scenario"]
    assert a["sensitivity"]["n_runs"] == 4 and a["sensitivity"]["line"] == r["line"]
    plain_summary = {k: v for k, v in a.items() if k != "sensitivity"}
    assert "Veriye bağlı pay" in [row[0] for row in ok("scenario.compare", scenarios=[a, a])["rows"]]
    assert "Veriye bağlı pay" not in [row[0] for row in ok("scenario.compare", scenarios=[plain_summary])["rows"]]


def test_refined_terrain_reports_its_original_grid(planned):
    ok("terrain.refine", terrain_id=planned, factor=2)
    r = ok("perimeter.sensitivity", terrain_id=planned, n_runs=4, max_px=200)
    assert r["assumptions"]["refine_factor"] == 2 and r["assumptions"]["base_cell_m"] == pytest.approx(20.0)


def _ring(terrain, x0, y0, x1, y1):
    """Rectangle (local metres) as an OSM ring of (lat, lon)."""
    from cctv_simulator.surface_obstacles import _Projector

    p = _Projector(terrain)
    return [(p.s + y / p.m_lat, p.w + x / p.m_lon) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0))]


def test_building_height_sources_are_counted_and_default_heights_widen_the_range(monkeypatch):
    t = TerrainData(np.zeros((120, 120), np.float32), cell_size_m=5.0, is_measured=True, lat_center=39.92,
                    lon_center=32.85, source_note="Terrarium (test)")
    entry = session.SESSION.add(dict(bare=t, label="düz"))

    def make(source):
        # low buildings beside the fence line (the plan's cameras look along it): their shadow is what the data decides
        return [obs.Feature(obs.KIND_BUILDING, 3.0, source, [_ring(t, x, 106, x + 12, 118)]) for x in (190, 280)]

    results = {}
    for source in ("tagged", "default"):
        monkeypatch.setattr(obs, "fetch_osm_obstacles", lambda bbox, s=source: make(s))
        ok("surface.osm", terrain_id=entry.id)
        ok("perimeter.plan", terrain_id=entry.id, camera=CAM, fence=[[100, 100], [500, 100], [500, 500], [100, 500]],
           target_ppm=40, mast_m=6)
        results[source] = ok("perimeter.sensitivity", terrain_id=entry.id, n_runs=8, dem_sigma_m=0.0, max_px=200,
                             level="detect")
    assert results["tagged"]["assumptions"]["objects"] == {"tagged": 2, "levels": 0, "default": 0}
    assert results["default"]["assumptions"]["objects"] == {"tagged": 0, "levels": 0, "default": 2}
    assert results["default"]["assumptions"]["source_known"] is True
    assert "2 varsayılan" in results["default"]["line"] and "2 etiketli" in results["tagged"]["line"]
    assert results["default"]["nominal"] == results["tagged"]["nominal"]                  # same data
    assert results["default"]["uncertain"] > results["tagged"]["uncertain"]
    off = ok("perimeter.sensitivity", terrain_id=entry.id, n_runs=4, dem_sigma_m=0.0, obstacle_uncertainty=0.0,
             max_px=200, level="detect")
    assert off["uncertain"] == 0.0 and "kesin kabul edildi" in off["line"]


def test_cancel_stops_the_job():
    tid = ok("terrain.procedural", preset="ridge_and_valley", grid=60, width_m=1200.0)["terrain_id"]
    ok("perimeter.plan", terrain_id=tid, camera=CAM, fence=FENCE, target_ppm=40, mast_m=6)
    out = io.StringIO()
    server = Server(METHODS, out=out)
    server.handle_line(json.dumps({"jsonrpc": "2.0", "id": 5, "method": "perimeter.sensitivity",
                                   "params": {"terrain_id": tid, "n_runs": 32}}))
    server.handle_line(json.dumps({"jsonrpc": "2.0", "method": "$/cancelRequest", "params": {"id": 5}}))
    server.wait_idle()
    reply = next(json.loads(x) for x in out.getvalue().splitlines() if json.loads(x).get("id") == 5)
    assert reply["error"]["code"] == REQUEST_CANCELLED
    assert "sensitivity" not in session.SESSION.get(tid).results


def test_validation_errors_are_turkish(planned):
    nothing = ok("terrain.procedural", preset="canyon", grid=50, width_m=800.0)["terrain_id"]
    reply, _ = call("perimeter.sensitivity", terrain_id=nothing)
    assert reply["error"]["code"] == APP_ERROR and "plan" in reply["error"]["message"]
    for bad, needle in ((dict(n_runs=2), "n_runs"), (dict(n_runs=99), "n_runs"), (dict(n_runs=6.5), "tam sayı"),
                        (dict(dem_sigma_m=-1), "dem_sigma_m"), (dict(dem_corr_m=0), "dem_corr_m"),
                        (dict(obstacle_uncertainty=9), "obstacle_uncertainty"), (dict(level="kötü"), "level")):
        reply, _ = call("perimeter.sensitivity", terrain_id=planned, **bad)
        assert reply["error"]["code"] == INVALID_PARAMS and needle in reply["error"]["message"], (bad, reply)
    reply, _ = call("perimeter.sensitivity", terrain_id="t99")
    assert reply["error"]["code"] == APP_ERROR and "oturumda yok" in reply["error"]["message"]
    reply, _ = call("perimeter.sensitivity", terrain_id=planned, level="identify", seed=-1)
    assert reply["error"]["code"] == INVALID_PARAMS
