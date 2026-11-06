"""Status panel: alarms and KPI tiles follow the optics engine's results."""
from dataclasses import replace

from cctv_simulator.calculations import analyze_dead_zone_coverage, calculate_for_camera
from cctv_simulator.models import DEFAULT_LEVELS, CameraConfig, TargetPoint
from cctv_simulator.status_summary import SEVERITY_ORDER, build_alarms, build_kpis

LEVELS = list(DEFAULT_LEVELS)


def _level(fragment):
    return next(level for level in LEVELS if fragment in level.name)


def _results(cameras, mode="min"):
    out = {cam.name: [calculate_for_camera(cam, mode, LEVELS)] for cam in cameras}
    analyze_dead_zone_coverage(out, list(cameras))
    return out


def test_kpis_read_the_engine_result():
    cam = CameraConfig()
    res = _results([cam])[cam.name]
    level = _level("Teşhis / Identification")
    tiles = build_kpis(res, level, None, 1)
    assert [t.label for t in tiles] == ["TASARIM MENZİLİ", "KÖR NOKTA", "GÖRÜŞ AÇISI", "KONTROL NOKTASI"]
    assert tiles[0].value == f"{res[0].ground_distances[level.key]:.1f}"
    assert tiles[0].state == "ok"
    assert tiles[1].value == f"{res[0].dead_zone_m:.1f}"
    assert tiles[2].value == f"{res[0].hfov_deg:.1f}"
    assert tiles[3].value == "—"


def test_unreachable_design_level_is_critical():
    cam = CameraConfig()
    res = _results([cam])
    level = _level("İnceleme")          # 1500 px/m on a 2.8 mm lens: in the dead zone
    assert build_kpis(res[cam.name], level, None, 1)[0].state == "danger"
    alarms = build_alarms([cam], res, level)
    assert alarms[0].severity == "danger"
    assert "hiçbir mesafede yok" in alarms[0].title
    assert alarms[0].camera_index == 0


def test_ir_alarm_names_the_level_not_a_bare_distance():
    cam = replace(CameraConfig(), ir_range_m=30.0)
    res = _results([cam])
    alarms = build_alarms([cam], res, _level("Teşhis / Identification"), levels=LEVELS)
    ir = [a for a in alarms if "IR" in a.title]
    assert ir and ir[0].severity == "warn"
    beyond = {k: d for k, d in res[cam.name][0].ground_distances.items() if d > 30.0}
    nearest = min(beyond, key=beyond.get)
    assert next(lv.name for lv in LEVELS if lv.key == nearest) in ir[0].detail


def test_alarms_are_sorted_by_severity_and_deduplicated_in_compare_mode():
    cam = replace(CameraConfig(), ir_range_m=10.0, min_lux=0.5)
    res = {cam.name: [calculate_for_camera(cam, m, LEVELS) for m in ("min", "max")]}
    alarms = build_alarms([cam], res, _level("İnceleme"), levels=LEVELS)
    ranks = [SEVERITY_ORDER[a.severity] for a in alarms]
    assert ranks == sorted(ranks)
    titles = [a.title for a in alarms]
    assert len(titles) == len(set(titles))


def test_uncovered_dead_zone_with_two_cameras_is_critical():
    a = replace(CameraConfig(), name="A", pos_x_m=0.0)
    # Back to back: neither cone reaches the other camera's dead zone.
    b = replace(CameraConfig(), name="B", pos_x_m=-500.0, heading_deg=180.0)
    res = _results([a, b])
    alarms = build_alarms([a, b], res, _level("Teşhis / Identification"))
    dz = [x for x in alarms if "kör nokta kapatılmıyor" in x.title]
    assert {x.camera_index for x in dz} == {0, 1}
    assert all(x.severity == "danger" for x in dz)


def test_target_point_failure_and_calc_error_surface():
    cam = CameraConfig()
    res = _results([cam])
    level = _level("Teşhis / Identification")
    far = TargetPoint(active=True, x_m=200.0, y_m=0.0)
    alarms = build_alarms([cam], res, level, far, 0, error="sıfıra bölme")
    assert alarms[0].title == "Hesap yapılamadı"
    assert any("sağlanmıyor" in a.title for a in alarms)
    tile = build_kpis(res[cam.name], level, far, 1)[3]
    assert tile.state == "danger" and tile.unit == "px/m"
    near = TargetPoint(active=True, x_m=3.0, y_m=0.0)
    assert build_kpis(res[cam.name], level, near, 1)[3].state == "ok"


def test_empty_results_give_placeholder_tiles():
    tiles = build_kpis([], LEVELS[0], None, 0)
    assert all(t.value == "—" for t in tiles)
