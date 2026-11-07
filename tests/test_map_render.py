"""map_render: the Tk-free map pictures shared by the classic map and the core service."""
import numpy as np

from cctv_simulator import map_render
from cctv_simulator.terrain_loader import TerrainData
from cctv_simulator.viewshed_3d import ZONE_DETECT, ZONE_IDENT


def _bump(south_west: bool = True) -> TerrainData:
    z = np.zeros((60, 60))
    r, c = (10, 10) if south_west else (50, 50)          # row 0 = south
    yy, xx = np.mgrid[0:60, 0:60]
    z += 40.0 * np.exp(-((yy - r) ** 2 + (xx - c) ** 2) / 40.0)
    return TerrainData(z, cell_size_m=10.0)


def test_pictures_are_north_up():
    """A hill in the south-west of z_grid (row 0 = south) is bottom-left in the picture."""
    for palette in ("classic", "console"):
        img = np.asarray(map_render.relief_image(_bump(), "elevation", palette=palette).convert("L"), float)
        h, w = img.shape
        bottom_left = img[h * 3 // 4:, : w // 4].mean()
        top_right = img[: h // 4, w * 3 // 4:].mean()
        assert bottom_left > top_right, palette


def test_console_palette_leaves_the_classic_look_alone():
    t = _bump()
    classic = np.asarray(map_render.relief_image(t, "elevation"))
    again = np.asarray(map_render.relief_image(t, "elevation", palette="classic"))
    console = np.asarray(map_render.relief_image(t, "elevation", palette="console"))
    assert np.array_equal(classic, again)
    assert classic.shape == console.shape and not np.array_equal(classic, console)
    # console relief stays dark so the cold DORI ramp is the brightest thing on the map
    ident = np.array(map_render.LAYER_BANDS["dori"][1][0][2][:3], float)
    assert console[..., :3].mean() < ident.mean() * 0.6


def test_zone_overlay_paints_only_banded_cells_with_legend_colours():
    g = np.zeros((40, 40), np.int8)
    g[5:15, 5:15] = ZONE_IDENT
    g[25:35, 25:35] = ZONE_DETECT
    img = np.asarray(map_render.zone_overlay(g, (400, 400)))
    assert img[..., 3][200:220, 0:20].max() == 0                     # empty cells stay transparent
    legend = map_render.legend("dori")[1]
    colours = np.array([rgba[:3] for _t, rgba in legend], float)

    def nearest(px):           # smoothing rounds by +-1
        d = np.abs(colours - px[:3].astype(float)).max(axis=1)
        return legend[int(d.argmin())][0], d.min()

    # middle of each block (north-up: grid row 10 is near the bottom of the picture)
    name, err = nearest(img[400 - 100, 100])
    assert name.startswith("Teşhis") and err <= 2
    name, err = nearest(img[400 - 300, 300])
    assert name.startswith("Algılama") and err <= 2
