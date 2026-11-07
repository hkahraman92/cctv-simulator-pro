"""Camera model comparison: same search per model, honest ranking."""
import numpy as np

from cctv_simulator.model_selection import compare_models
from cctv_simulator.models import CameraConfig
from cctv_simulator.network_plan import PriceBook
from cctv_simulator.pole_optimizer import minimize_poles
from cctv_simulator.terrain_loader import TerrainData
from cctv_simulator.viewshed_3d import ZONE_DETECT

T = TerrainData(np.zeros((100, 100)), cell_size_m=5.0)
FENCE = [(50.0, 250.0), (450.0, 250.0)]
KW = dict(target_ppm=40.0, mast_height_m=6.0, is_closed_loop=False)
WIDE = CameraConfig(name="2MP", model_name="2MP", resolution_name="2 MP (1080p - 1920x1080)", focal_min_mm=2.8, focal_max_mm=12.0)
SHARP = CameraConfig(name="8MP", model_name="8MP", resolution_name="8 MP (4K - 3840x2160)", focal_min_mm=2.8, focal_max_mm=12.0)


def test_each_model_gets_the_pole_search_and_better_resolution_needs_fewer_poles():
    opts = compare_models(T, FENCE, [("2MP", WIDE, {}), ("8MP", SHARP, {})], plan_kwargs=KW, goal_frac=0.6,
                          level=ZONE_DETECT)
    by = {o.model_name: o for o in opts}
    direct = minimize_poles(T, FENCE, WIDE, plan_kwargs=KW, goal_frac=0.6, level=ZONE_DETECT)
    assert by["2MP"].poles == direct.best.fixed_poles and by["2MP"].met == direct.met
    assert by["8MP"].poles <= by["2MP"].poles
    assert all(o.network is not None for o in opts)


def test_ranking_uses_cost_only_when_everything_is_priced():
    book = PriceBook(pole=8000, switch=6000, copper_per_m=40, fibre_per_m=20, nvr_per_tb=4000, nvr_base=20000)
    opts = compare_models(T, FENCE, [("2MP", WIDE, {}), ("8MP", SHARP, {})], plan_kwargs=KW, goal_frac=0.6,
                          level=ZONE_DETECT, price_book=book, prices={"2MP": 3000, "8MP": 60000})
    met = [o for o in opts if o.met]
    assert [o.cost.total for o in met] == sorted(o.cost.total for o in met)
    assert all(not o.cost.unpriced for o in opts)
