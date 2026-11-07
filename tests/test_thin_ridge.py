"""Thin ridges / walls between ray samples: node-line crossings join the horizon."""
import math

import numpy as np

from cctv_simulator.terrain_loader import TerrainData
from cctv_simulator.viewshed_3d import march_rays, ray_step_size

WALL_H, CAM_Z, CAM_X, WALL_X = 1.0, 2.0, 100.0, 200.0


def _site():
    z = np.zeros((80, 80))
    z[:, int(WALL_X / 5)] = WALL_H          # one node line (x = 200 m): a 1 m thin wall
    return TerrainData(z, cell_size_m=5.0)


def _leaks(crossings: bool, angle_deg: float) -> int:
    """Ground samples behind the wall that geometry says MUST be hidden (the
    sightline passes the wall below 95 % of its height) but read visible."""
    t = _site()
    ang = math.radians(angle_deg)
    steps = np.arange(1, 400) * ray_step_size(t.cell_size_m)
    rm = march_rays(t, CAM_X, 200.0, CAM_Z, np.array([ang]), steps, earth_curvature=False,
                    occluder_crossings=crossings)
    d = steps[:rm.valid.shape[1]]
    d_wall = (WALL_X - CAM_X) / math.sin(ang)
    with np.errstate(divide="ignore", invalid="ignore"):
        los_at_wall = CAM_Z * (1.0 - d_wall / d)           # sightline height above the wall's foot
    must_hide = rm.valid[0] & (d > d_wall * 1.05) & (los_at_wall < 0.95 * WALL_H)
    return int((must_hide & rm.los_clear[0]).sum())


ANGLES = np.linspace(35.0, 90.0, 56)


def test_every_ray_respects_a_thin_wall():
    assert sum(_leaks(True, a) for a in ANGLES) == 0


def test_the_old_sampling_leaked_through_it():
    # documents the bug fixed: samples straddling the node line read the wall low
    assert sum(_leaks(False, a) for a in ANGLES) > 0
