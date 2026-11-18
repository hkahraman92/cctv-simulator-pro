"""refine_terrain: a finer analysis grid of the SAME terrain (extent-preserving convention)."""
from __future__ import annotations

import numpy as np
import pytest

from cctv_simulator import map_render
from cctv_simulator import surface_obstacles as so
from cctv_simulator.models import CameraConfig
from cctv_simulator.terrain_loader import TerrainData, generate_procedural_terrain, refine_terrain
from cctv_simulator.viewshed_3d import CameraPlacement, calculate_multi_camera_viewshed

CAM = CameraConfig(name="V", sensor_name='1/2.8"', resolution_name="4 MP (2K - 2688x1520)",
                   focal_min_mm=2.8, focal_max_mm=12.0)


def _hills(n=41, cell=10.0, **kw):
    t = generate_procedural_terrain("ridge_and_valley", grid_size=n, cell_size_m=cell)
    t.lat_center, t.lon_center, t.is_measured = 39.92, 32.85, True
    for k, v in kw.items():
        setattr(t, k, v)
    return t


@pytest.mark.parametrize("factor", [1, 2, 3, 4])
def test_original_nodes_are_bit_identical(factor):
    t = _hills()
    r = refine_terrain(t, factor)
    assert r.z_grid.shape == (t.rows * factor, t.cols * factor)
    assert np.array_equal(r.z_grid[::factor, ::factor], t.z_grid)
    # the factor - 1 extra nodes past the last original row / column repeat the last node
    assert np.array_equal(r.z_grid[-1, ::factor], t.z_grid[-1])
    assert np.array_equal(r.z_grid[(t.rows - 1) * factor:, 0], np.full(factor, t.z_grid[-1, 0]))


@pytest.mark.parametrize("factor", [2, 4])
def test_elevation_function_is_unchanged(factor):
    t = _hills()
    r = refine_terrain(t, factor)
    rng = np.random.default_rng(7)
    ext = (t.cols - 1) * t.cell_size_m        # inside the original node extent
    for x, y in rng.uniform(0.0, ext, size=(300, 2)):
        assert r.get_elevation_at(x, y) == pytest.approx(t.get_elevation_at(x, y), abs=1e-9)
        assert r.ground_elevation_at(x, y) == pytest.approx(t.ground_elevation_at(x, y), abs=1e-9)


def test_extent_origin_and_flags_preserved():
    t = _hills(origin_x=12.0, origin_y=-7.0, source_note="DEM", georef={"epsg": 32636, "x0": 1.0, "y0": 2.0,
                                                                          "sx": 30.0, "sy": 30.0, "resampled": False})
    r = refine_terrain(t, 4)
    assert (r.origin_x, r.origin_y) == (12.0, -7.0)
    assert r.cell_size_m == t.cell_size_m / 4
    # the extent is preserved: width_m / height_m / the geographic centre are not touched
    assert r.width_m == pytest.approx(t.width_m, rel=1e-12) and r.height_m == pytest.approx(t.height_m, rel=1e-12)
    assert (r.lat_center, r.lon_center) == (t.lat_center, t.lon_center)
    assert r.is_measured and r.source_note == "DEM" and r.name == t.name
    assert r.georef == t.georef and r.georef is not t.georef
    assert r.z_grid.dtype == t.z_grid.dtype and not r.has_obstacles
    from cctv_simulator.online_map_loader import calculate_bbox
    assert calculate_bbox(r.lat_center, r.lon_center, r.width_m, r.height_m) == pytest.approx(
        calculate_bbox(t.lat_center, t.lon_center, t.width_m, t.height_m), abs=1e-12)
    assert t.z_grid.shape == (41, 41)                  # the input is untouched


def test_exact_extent_for_power_of_two_factors():
    t = _hills()
    for f in (2, 4, 8):
        r = refine_terrain(t, f)
        assert r.width_m == t.width_m and r.height_m == t.height_m


def test_factor_one_is_an_equivalent_copy_and_bad_factor_raises():
    t = _hills()
    r = refine_terrain(t, 1)
    assert np.array_equal(r.z_grid, t.z_grid) and r.z_grid is not t.z_grid and r.cell_size_m == t.cell_size_m
    with pytest.raises(ValueError):
        refine_terrain(t, 0)


def _with_block(n=41, cell=10.0, z=100.0, rows=(15, 18), cols=(10, 30), h=30.0):
    t = TerrainData(z_grid=np.full((n, n), z), cell_size_m=cell, lat_center=39.92, lon_center=32.85)
    hg = np.zeros((n, n))
    kg = np.zeros((n, n), np.uint8)
    hg[rows[0]:rows[1], cols[0]:cols[1]] = h
    kg[rows[0]:rows[1], cols[0]:cols[1]] = so.KIND_BUILDING
    return so.with_obstacles(t, so.ObstacleLayer(height_grid=hg, kind_grid=kg, counts={}))


def test_obstacle_footprint_area_is_preserved_and_heights_not_smeared():
    t = _with_block()
    r = refine_terrain(t, 4)
    assert r.has_obstacles and r.ground_z_grid.shape == r.z_grid.shape
    foot0 = (t.obstacle_grid > 0).sum() * t.cell_size_m ** 2
    foot1 = (r.obstacle_grid > 0).sum() * r.cell_size_m ** 2
    perimeter = 2 * (3 + 20) * t.cell_size_m            # original cells' outline
    assert abs(foot1 - foot0) <= perimeter * r.cell_size_m          # within one fine cell ring
    assert set(np.unique(r.obstacle_grid)) == {0.0, 30.0}
    assert set(np.unique(r.obstacle_kind)) == {0, so.KIND_BUILDING}
    assert np.array_equal(r.z_grid, r.ground_z_grid + r.obstacle_grid)
    # original nodes still exact
    assert np.array_equal(r.z_grid[::4, ::4], t.z_grid)
    assert np.array_equal(r.obstacle_grid[::4, ::4], t.obstacle_grid)


def test_obstacles_on_slope_sit_on_the_refined_ground():
    base = _hills(n=31)
    hg = np.zeros(base.z_grid.shape)
    hg[10:13, 10:13] = 12.0
    t = so.with_obstacles(base, so.ObstacleLayer(height_grid=hg, kind_grid=(hg > 0).astype(np.uint8), counts={}))
    r = refine_terrain(t, 2)
    bare = refine_terrain(base, 2)
    assert np.array_equal(r.ground_z_grid, bare.z_grid)
    for x, y in ((50.0, 50.0), (101.3, 99.9)):
        assert r.ground_elevation_at(x, y) == pytest.approx(t.ground_elevation_at(x, y), abs=1e-9)


def test_a_wall_still_shadows_after_refinement():
    t = _with_block(n=61, rows=(33, 35), cols=(20, 40), h=40.0)
    flat = TerrainData(z_grid=np.full((61, 61), 100.0), cell_size_m=10.0)
    pl = [CameraPlacement(300.0, 300.0, 8.0, CAM, pan_deg=0, tilt_deg=-8, max_range_m=250.0, lens_mode="min")]
    for f in (1, 2, 4):
        walled = calculate_multi_camera_viewshed(refine_terrain(t, f), pl)
        open_ = calculate_multi_camera_viewshed(refine_terrain(flat, f), pl)
        assert walled.occluded_area_m2 > 0.05 * open_.visible_area_m2
        assert walled.visible_area_m2 < open_.visible_area_m2


def test_viewshed_on_flat_terrain_converges_to_the_analytic_wedge():
    t0 = TerrainData(z_grid=np.full((61, 61), 100.0), cell_size_m=10.0)
    pl = [CameraPlacement(300.0, 300.0, 8.0, CAM, pan_deg=0, tilt_deg=-8, max_range_m=250.0, lens_mode="min")]
    mv = calculate_multi_camera_viewshed(t0, pl)
    size = int(t0.width_m / 0.5)                      # 0.5 m picture pixels: the reference wedge
    cl = map_render.multi_view_classes(t0, mv, pl, (size, size), pixel_budget=10 ** 9)
    ref = float(cl.visible.sum()) * 0.25
    err = {f: abs(calculate_multi_camera_viewshed(refine_terrain(t0, f), pl).visible_area_m2 - ref) / ref
           for f in (1, 2, 8)}
    # Pan 0 + a 10 m cell puts the cone edges through grid nodes; the closed rule over-counted them
    # (+11.7 % at 10 m, +7.1 % / +3.9 % / +1.2 % at f = 2 / 4 / 8), which is what made this a monotone
    # convergence. With half-open ties (viewshed_3d "Boundary ties") the aligned view is as unbiased as
    # any: -2.3 % / +0.07 % / +0.07 % / -0.6 %, i.e. only the coarse cell is still rough (3-cell wedge).
    assert err[1] > 0.02                               # the 10 m cell is rough (3-cell wedge)
    assert err[2] < err[1]
    assert err[2] < 0.01 and err[8] < 0.02
