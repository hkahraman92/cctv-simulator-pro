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


def test_legend_rows_carry_the_level_key_only_for_level_rows():
    keyed = map_render.legend("dori", keys=True)[1]
    assert [r[2] for r in keyed if len(r) == 3] == ["identify", "recognize", "observe", "detect"]
    assert len(keyed[-1]) == 2                                    # the blind-spot row has no key
    assert not any(r[2] == "monitor" for r in keyed if len(r) == 3)          # a default layer: no monitor row
    mon = map_render.legend("dori_monitor", keys=True)[1]
    row = next(r for r in mon if r[0].startswith("İzleme"))
    assert row[2] == "monitor"
    assert map_render.legend("overlap", keys=True)[1] == map_render.legend("overlap")[1]   # not a level layer
    # the plain form (Tk window, share HTML) is unchanged
    assert all(len(r) == 2 for r in map_render.legend("dori_monitor")[1])


def test_service_legend_marks_the_monitor_row():
    import dataclasses

    from cctv_simulator.models import CameraConfig
    from cctv_simulator.service import session
    from cctv_simulator.service.methods import METHODS
    from cctv_simulator.service.protocol import Server
    import io, json

    session.reset()

    def ok(method, **params):
        out = io.StringIO()
        server = Server(METHODS, out=out)
        server.handle_line(json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}))
        server.wait_idle()
        return next(json.loads(x) for x in out.getvalue().splitlines() if '"id": 1' in x or '"id":1' in x)["result"]

    tid = ok("terrain.procedural", preset="ridge_and_valley", grid=80, width_m=800.0)["terrain_id"]
    cam = dataclasses.asdict(CameraConfig(focal_min_mm=2.8, focal_max_mm=12.0))
    args = dict(terrain_id=tid, camera=cam, x_m=400, y_m=400, mast_m=8, pan_deg=0, tilt_deg=-8, range_m=400,
                lens_mode="max", max_px=200)
    mon = ok("viewshed.single", **args, level="monitor")["legend"]
    assert next(r for r in mon if r[0].startswith("İzleme"))[2] == "monitor"
    assert all(len(r) == 2 or r[2] != "monitor" for r in ok("viewshed.single", **args)["legend"])
