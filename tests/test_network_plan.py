"""Cabling / network plan and cost estimate of a perimeter layout."""
import numpy as np
import pytest

from cctv_simulator.models import CameraConfig
from cctv_simulator.network_plan import PriceBook, camera_power_w, estimate_cost, plan_network
from cctv_simulator.perimeter_planner import generate_perimeter_plan
from cctv_simulator.terrain_loader import TerrainData

CAM = CameraConfig(focal_min_mm=2.8, focal_max_mm=12.0)


@pytest.fixture
def plan():
    t = TerrainData(np.zeros((120, 120)), cell_size_m=5.0)
    return generate_perimeter_plan(t, [(50.0, 300.0), (550.0, 300.0)], CAM, target_ppm=40, mast_height_m=6,
                                   is_closed_loop=False, spacing_m=25.0)


def test_every_pole_on_exactly_one_switch_within_the_rules(plan):
    net = plan_network(plan, switch_ports=8, poe_budget_w=120, max_copper_m=90)
    ids = sorted(p for s in net.switches for p in s.poles)
    assert ids == sorted(c.pole_id for c in plan.placed_cameras)
    assert all(s.ports_used <= 8 and s.poe_w <= 120 for s in net.switches)
    assert net.longest_copper_m <= 90 + 1e-9
    assert net.copper_m > 0 and net.fibre_m > 0
    # tighter rules can only add switches
    assert len(plan_network(plan, switch_ports=4).switches) >= len(net.switches)
    assert len(plan_network(plan, max_copper_m=40).switches) >= len(net.switches)


def test_storage_and_power_are_the_stated_formulas(plan):
    net = plan_network(plan, bitrate_fixed_mbps=4.0, retention_days=30, raid_overhead=0.15)
    n = len(plan.placed_cameras)
    assert net.bitrate_mbps == pytest.approx(4.0 * n)
    assert net.storage_tb == pytest.approx(4.0 * n * 1e6 / 8 * 86400 * 30 / 1e12 * 1.15)
    assert net.poe_total_w == pytest.approx(12.0 * n)                     # default 12 W, stated
    assert camera_power_w({"power_consumption": "≤ 12,5 W"}) == (12.5, True)
    assert camera_power_w({}, ptz=True) == (30.0, False)


def test_cost_names_what_is_not_priced(plan):
    net = plan_network(plan)
    c = estimate_cost(plan, net, PriceBook(camera=10_000, pole=5_000))
    n = len(plan.placed_cameras)
    assert c.total == pytest.approx(10_000 * n + 5_000 * plan.pole_count)
    assert "PoE switch (dış ortam)" in c.unpriced and "Sabit kamera" not in c.unpriced
