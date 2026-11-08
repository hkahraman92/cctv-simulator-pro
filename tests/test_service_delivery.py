"""Core service deliverables: KML / DXF, night coverage, scenarios, share page, offer package."""
from __future__ import annotations

import dataclasses
import io
import json
import zipfile

import pytest

from cctv_simulator.models import CameraConfig
from cctv_simulator.service import session
from cctv_simulator.service.methods import METHODS
from cctv_simulator.service.protocol import APP_ERROR, Server

FIX = dataclasses.asdict(CameraConfig(name="Sabit", focal_min_mm=2.8, focal_max_mm=12.0, ir_range_m=30.0,
                                      min_lux=0.05))
FENCE = [[150, 150], [850, 150], [850, 850], [150, 850]]


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
def planned():
    session.reset()
    tid = ok("terrain.procedural", preset="ridge_and_valley", grid=100, width_m=1000.0)["terrain_id"]
    ok("perimeter.plan", terrain_id=tid, camera=FIX, fence=FENCE, target_ppm=40, mast_m=6)
    return tid


def test_site_export_needs_a_position_for_kml_but_writes_dxf(planned, tmp_path):
    err = call("export.site", terrain_id=planned, path=str(tmp_path / "a.kml"))
    assert err["error"]["code"] == APP_ERROR and "coğrafi konumu yok" in err["error"]["message"]
    r = ok("export.site", terrain_id=planned, path=str(tmp_path / "a.dxf"))
    assert (tmp_path / "a.dxf").read_text(encoding="ascii").rstrip().endswith("EOF")
    assert r["exact_georef"] is False


def test_kmz_with_network_and_coverage_once_a_position_is_known(planned, tmp_path):
    t = session.SESSION.get(planned).bare
    t.lat_center, t.lon_center = 39.92, 32.85
    ok("network.plan", terrain_id=planned)
    ok("perimeter.coverage", terrain_id=planned, max_px=300)
    r = ok("export.site", terrain_id=planned, path=str(tmp_path / "saha.kmz"))
    assert r["with_network"] and r["with_coverage"]
    with zipfile.ZipFile(tmp_path / "saha.kmz") as z:
        doc = z.read("doc.kml").decode("utf-8")
    assert "SW1" in doc and "GroundOverlay" in doc


def test_night_coverage_is_the_engine_with_the_ir_cap(planned):
    from cctv_simulator.focal_optimizer import band_area, fence_roi
    from cctv_simulator.night import camera_night_mode, capped_placements
    from cctv_simulator.viewshed_3d import ZONE_DETECT

    # moonless winter night, IR 30 m
    r = ok("night.coverage", terrain_id=planned, datetime="2024-01-11T23:00", lat=39.92, lon=32.85, utc_offset_h=3,
           max_px=300)
    assert r["mode"]["mode"] == "ir" and r["mode"]["range_cap_m"] == 30.0
    assert r["night_band_share"]["detect"] < r["day_band_share"]["detect"]
    entry = session.SESSION.get(planned)
    plan, cam, kw, fence = entry.results["plan"]
    t = entry.terrain(True)
    roi = fence_roi(t, fence, kw["is_closed_loop"], 40.0)
    mode = camera_night_mode(cam.sensor_name, cam.model_name, cam.min_lux, cam.ir_range_m, r["condition"]["lux"])
    _mv, area = band_area(t, plan, cam, roi, ZONE_DETECT, visibility_km=kw["visibility_km"], weather=kw["weather"],
                          transform=lambda p: capped_placements(p, mode))
    assert r["night_band_share"]["detect"] == pytest.approx(area / r["band_area_m2"])
    # a bright midday: no light cap, night share == day share
    d = ok("night.coverage", terrain_id=planned, datetime="2024-06-21T12:00", lat=39.92, lon=32.85, max_px=300)
    assert d["mode"]["mode"] == "pasif" and d["night_band_share"] == d["day_band_share"]
    # IR off on a dark night: nothing seen
    z = ok("night.coverage", terrain_id=planned, datetime="2024-01-11T23:00", lat=39.92, lon=32.85, ir_on=False,
           max_px=300)
    assert z["mode"]["mode"] == "yok" and z["night_band_share"]["detect"] == 0.0 and "overlay" not in z


def test_timeline_reports_the_mode_per_step():
    r = ok("night.timeline", lat=39.92, lon=32.85, date="2024-01-11", step_min=60, camera=FIX)
    modes = {s["mode"] for s in r["steps"]}
    assert len(r["steps"]) == 25 and {"pasif", "ir"} <= modes


def test_scenarios_snapshot_and_compare(planned):
    ok("network.plan", terrain_id=planned, prices={"camera": 5000, "pole": 3000})
    ok("perimeter.coverage", terrain_id=planned, max_px=300)
    a = ok("scenario.snapshot", terrain_id=planned, label="A")["scenario"]
    assert a["poles"] > 0 and a["band_share"]["detect"] is not None and a["cost_total"] > 0
    ok("perimeter.plan", terrain_id=planned, camera=FIX, fence=FENCE, target_ppm=80, mast_m=6)
    b = ok("scenario.snapshot", terrain_id=planned, label="B")["scenario"]
    assert b["poles"] > a["poles"] and b["band_share"]["detect"] is None       # coverage not re-run for B
    r = ok("scenario.compare", scenarios=[a, b])
    assert r["rows"][0] == ["Ölçüt", "A", "B"]
    assert r["best"]["poles"] == 0
    assert call("scenario.compare", scenarios=[])["error"]["code"] != APP_ERROR


def test_share_page_is_self_contained(planned, tmp_path):
    ok("perimeter.coverage", terrain_id=planned, max_px=300)
    ok("export.share_html", terrain_id=planned, path=str(tmp_path / "p.html"), title="Saha")
    text = (tmp_path / "p.html").read_text(encoding="utf-8")
    assert "<script" not in text and "http://" not in text and "https://" not in text
    assert "data:image/jpeg;base64," in text and "data:image/png;base64," in text
    assert "TEMSİLİ" in text or "ölçülmüş değil" in text


def test_offer_package_has_the_parts_and_names_what_is_missing(planned, tmp_path):
    ok("network.plan", terrain_id=planned, prices={"camera": 5000})
    r = ok("export.package", terrain_id=planned, path=str(tmp_path / "teklif.zip"), project_name="Tesis")
    with zipfile.ZipFile(tmp_path / "teklif.zip") as z:
        names = set(z.namelist())
        readme = z.read("00-BENIOKU.txt").decode("utf-8-sig")
    assert {"00-BENIOKU.txt", "01-metraj-maliyet.csv", "01-metraj.xlsx", "02-direk-listesi.csv", "03-ag-plani.csv",
            "04-muhendislik-raporu.pdf", "06-saha.dxf", "07-paylasim.html", "08-ozet.json"} <= names
    assert "06-saha.kmz" not in names                       # procedural terrain: no position
    assert any("uygunluk" in s for s in r["skipped"]) and any("KMZ" in s for s in r["skipped"])
    assert "UYARI" in readme
