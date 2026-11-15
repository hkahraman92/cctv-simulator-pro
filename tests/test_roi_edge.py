"""Protected-area masks are area-unbiased for fences on grid lines and unchanged elsewhere.

A node exactly ``band`` from the line used to count on both sides (``<=``): an aligned band was
2 * band + cell wide. Only exact ties changed; every other node keeps the old ``d <= band`` verdict."""
from __future__ import annotations

import math

import numpy as np
import pytest

from cctv_simulator.area_planner import polygon_mask
from cctv_simulator.focal_optimizer import fence_roi
from cctv_simulator.terrain_loader import TerrainData

SQUARE = [(600.0, 600.0), (1400.0, 600.0), (1400.0, 1400.0), (600.0, 1400.0)]


def _terrain(cell, width=2000.0):
    n = int(width / cell)
    return TerrainData(np.zeros((n, n), np.float32), cell_size_m=cell)


def _old_rule(t, fence, closed, band, inside_m=None, side=None):
    """Frozen copy of the pre-fix mask: ``distance <= band`` on both sides, the plain side rule."""
    from cctv_simulator.area_planner import _inside

    pts = list(fence)
    if closed and len(pts) > 2 and tuple(pts[0]) != tuple(pts[-1]):
        pts = pts + [pts[0]]
    cols = t.origin_x + np.arange(t.cols) * t.cell_size_m
    rows = t.origin_y + np.arange(t.rows) * t.cell_size_m
    mx, my = np.meshgrid(cols, rows)

    def dist2(px_, py_):
        d2 = np.full(mx.shape, np.inf, dtype=np.float32)
        for (x0, y0), (x1, y1) in zip(pts[:-1], pts[1:]):
            vx, vy = x1 - x0, y1 - y0
            seg2 = vx * vx + vy * vy
            if seg2 < 1e-6:
                continue
            u = np.clip(((px_ - x0) * vx + (py_ - y0) * vy) / seg2, 0.0, 1.0)
            qx, qy = x0 + u * vx, y0 + u * vy
            d2 = np.minimum(d2, (px_ - qx) ** 2 + (py_ - qy) ** 2)
        return d2

    def band_(b):
        return dist2(mx, my) <= b * b

    if side in ("left", "right"):
        d2 = np.full(mx.shape, np.inf, dtype=np.float64)
        left = np.zeros(mx.shape, bool)
        for (x0, y0), (x1, y1) in zip(pts[:-1], pts[1:]):
            vx, vy = x1 - x0, y1 - y0
            seg2 = vx * vx + vy * vy
            if seg2 < 1e-6:
                continue
            u = np.clip(((mx - x0) * vx + (my - y0) * vy) / seg2, 0.0, 1.0)
            dd = (mx - (x0 + u * vx)) ** 2 + (my - (y0 + u * vy)) ** 2
            nearer = dd < d2
            d2 = np.where(nearer, dd, d2)
            left = np.where(nearer, vx * (my - y0) - vy * (mx - x0) > 0, left)
        return (d2 <= band * band) & (left if side == "left" else ~left)
    if inside_m is None or not closed or len(pts) < 4 or abs(inside_m - band) < 1e-9:
        return band_(band)
    inner = _inside(mx, my, pts)
    return (band_(band) & ~inner) | (band_(inside_m) & inner)


def _band_area_analytic(side, band):
    outer = side * side + 4 * side * band + math.pi * band * band
    inner = (side - 2 * band) ** 2
    return outer - inner


@pytest.mark.parametrize("cell", [10.0, 5.0, 2.5])
def test_aligned_square_band_area_is_the_analytic_one(cell):
    t = _terrain(cell)
    roi = fence_roi(t, SQUARE, True, 40.0)
    area = float(roi.sum()) * cell * cell
    want = _band_area_analytic(800.0, 40.0)
    # within one ring of cells around the whole band (perimeter x cell)
    assert abs(area - want) <= 3200.0 * cell * 0.5, (area, want)
    old = float(_old_rule(t, SQUARE, True, 40.0).sum()) * cell * cell
    assert abs(area - want) < abs(old - want)


@pytest.mark.parametrize("cell", [10.0, 5.0, 2.5])
def test_aligned_straight_edge_is_exactly_band_over_cell_nodes_across(cell):
    t = _terrain(cell)
    roi = fence_roi(t, SQUARE, True, 40.0)
    mid_col = int(1000.0 / cell)
    for x_line_row, cols_ in ((600.0, mid_col),):
        pass
    # south edge (y = 600) and north edge (y = 1400): 2 * 40 / cell nodes across the line, line counted once
    for y_line in (600.0, 1400.0):
        r0 = int(round((y_line - 40.0) / cell)) - 2
        r1 = int(round((y_line + 40.0) / cell)) + 3
        assert int(roi[r0:r1, mid_col].sum()) == int(80.0 / cell)
    row_mid = int(1000.0 / cell)
    for x_line in (600.0, 1400.0):
        c0 = int(round((x_line - 40.0) / cell)) - 2
        c1 = int(round((x_line + 40.0) / cell)) + 3
        assert int(roi[row_mid, c0:c1].sum()) == int(80.0 / cell)


def test_aligned_mask_does_not_depend_on_the_vertex_order():
    t = _terrain(5.0)
    a = fence_roi(t, SQUARE, True, 40.0)
    b = fence_roi(t, SQUARE[::-1], True, 40.0)
    c = fence_roi(t, SQUARE[2:] + SQUARE[:2], True, 40.0)
    assert np.array_equal(a, b) and np.array_equal(a, c)


@pytest.mark.parametrize("cell", [10.0, 5.0, 2.5])
def test_off_grid_masks_are_exactly_the_old_rule(cell):
    t = _terrain(cell)
    for dx, dy in ((3.7, 1.3), (1.1, 6.2), (7.3, 4.4), (0.37, 8.91)):
        fence = [(x + dx, y + dy) for x, y in SQUARE]
        assert np.array_equal(fence_roi(t, fence, True, 40.0), _old_rule(t, fence, True, 40.0)), (dx, dy)
        assert np.array_equal(fence_roi(t, fence, True, 40.0, 12.0), _old_rule(t, fence, True, 40.0, 12.0))
        assert np.array_equal(fence_roi(t, fence[:3], False, 40.0), _old_rule(t, fence[:3], False, 40.0))
        for side in ("left", "right"):
            assert np.array_equal(fence_roi(t, fence[:3], False, 40.0, None, side),
                                  _old_rule(t, fence[:3], False, 40.0, None, side)), side


def test_slanted_off_grid_polyline_is_unchanged():
    t = _terrain(10.0)
    line = [(213.3, 377.9), (911.1, 640.4), (1500.7, 1203.2)]
    assert np.array_equal(fence_roi(t, line, False, 55.0), _old_rule(t, line, False, 55.0))


def test_inner_strip_aligned_is_also_half_open():
    t = _terrain(5.0)
    roi = fence_roi(t, SQUARE, True, 40.0, 10.0)
    mid = int(1000.0 / 5.0)
    col = roi[:, mid]
    south = col[int((600 - 60) / 5):int((600 + 60) / 5)].sum()
    north = col[int((1400 - 60) / 5):int((1400 + 60) / 5)].sum()
    assert south == north == int((40 + 10) / 5)


def test_side_band_is_band_over_cell_deep_on_both_sides():
    t = _terrain(5.0)
    line = [(600.0, 1000.0), (1400.0, 1000.0)]
    mid = int(1000.0 / 5.0)
    row = int(1000.0 / 5.0)
    left = fence_roi(t, line, False, 40.0, None, "left")
    right = fence_roi(t, line, False, 40.0, None, "right")
    assert int(left[:, mid].sum()) == 8 and int(right[:, mid].sum()) == 8
    assert not (left & right).any()
    assert left[row + 1, mid] and not left[row, mid] and right[row, mid]


def test_thin_fence_line_mask_stays_inclusive_and_non_empty():
    for cell in (10.0, 5.0, 1.0):
        t = _terrain(cell, 2000.0 if cell > 1 else 1500.0)
        line = fence_roi(t, SQUARE, True, max(0.75 * cell, 1.0))
        assert line.sum() > 0
        assert np.array_equal(line, _old_rule(t, SQUARE, True, max(0.75 * cell, 1.0)))


@pytest.mark.parametrize("cell", [10.0, 5.0, 2.5])
def test_aligned_polygon_area_is_exact(cell):
    t = _terrain(cell)
    rect = [(500.0, 700.0), (1300.0, 700.0), (1300.0, 1200.0), (500.0, 1200.0)]
    assert float(polygon_mask(t, rect).sum()) * cell * cell == 800.0 * 500.0
    assert float(polygon_mask(t, rect[::-1]).sum()) * cell * cell == 800.0 * 500.0
