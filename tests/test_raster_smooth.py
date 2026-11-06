"""Display smoothing: same areas and colours as nearest-neighbour, no stairs."""
import numpy as np
import pytest

from cctv_simulator.raster_smooth import smooth_overlay

A = (40, 120, 200, 180)
B = (150, 210, 240, 200)


def _diagonal(n=40):
    ov = np.zeros((n, n, 4), np.uint8)
    yy, xx = np.mgrid[0:n, 0:n]
    ov[xx > yy] = A                      # a 45-degree staircase edge
    return ov


def test_area_and_colours_match_nearest():
    ov = _diagonal()
    out = np.asarray(smooth_overlay(ov, (240, 240)))
    near = np.asarray(__import__("PIL.Image", fromlist=["Image"]).fromarray(ov, "RGBA")
                      .resize((240, 240), 0))
    cover = (out[..., 3] > 90).mean()
    assert cover == pytest.approx((near[..., 3] > 0).mean(), abs=0.02)
    solid = out[..., 3] == A[3]
    assert solid.any() and np.all(out[solid][:, :3] == A[:3])


def test_staircase_becomes_a_line():
    out = np.asarray(smooth_overlay(_diagonal(), (240, 240)))
    # nearest: the edge column of each row jumps in 6 px steps; smooth: ~1 px
    edge = np.array([int(np.argmax(row > 90)) for row in out[..., 3][10:230]])
    assert np.abs(np.diff(edge)).max() <= 2


def test_two_classes_touch_without_a_gap():
    ov = np.zeros((20, 20, 4), np.uint8)
    ov[:, :10] = A
    ov[:, 10:] = B
    out = np.asarray(smooth_overlay(ov, (120, 120)))
    assert out[60, 5:115, 3].min() >= min(A[3], B[3]) - 1     # no transparent seam


def test_continuous_ramp_falls_back_to_nearest_and_empty_is_empty():
    ramp = np.zeros((10, 10, 4), np.uint8)
    ramp[..., 0] = np.arange(100).reshape(10, 10)
    ramp[..., 3] = 255
    out = np.asarray(smooth_overlay(ramp, (50, 50)))
    assert out[0, 0, 0] == 0 and out[-1, -1, 0] == 99
    blank = np.asarray(smooth_overlay(np.zeros((5, 5, 4), np.uint8), (30, 20)))
    assert blank.shape == (20, 30, 4) and blank[..., 3].max() == 0


def test_wedge_keeps_only_the_field_of_view():
    from PIL import Image

    from cctv_simulator.raster_smooth import clip_to_wedge
    img = Image.new("RGBA", (200, 200), A)
    out = np.asarray(clip_to_wedge(img, (100, 190), (1.0, 1.0), 0.0, 40.0, 150.0))
    assert out[100, 100, 3] == A[3]          # straight ahead (north), in range
    assert out[100, 20, 3] == 0              # far left of the 40-degree cone
    assert out[10, 100, 3] == 0              # beyond the reach
    assert out[195, 100, 3] == 0             # behind the camera
    full = clip_to_wedge(img, (100, 100), (1.0, 1.0), 0.0, 360.0, 50.0)
    assert np.asarray(full)[0, 0, 3] == A[3]  # a full turn is not clipped


def test_small_content_is_cropped_but_lands_in_place():
    ov = np.zeros((200, 200, 4), np.uint8)
    ov[150:160, 30:40] = A                              # a 10x10 patch in a big grid
    out = np.asarray(smooth_overlay(ov, (800, 800)))
    ys, xs = np.nonzero(out[..., 3] > 90)
    assert 590 <= ys.min() <= 610 and 630 <= ys.max() <= 650
    assert 110 <= xs.min() <= 130 and 150 <= xs.max() <= 170
