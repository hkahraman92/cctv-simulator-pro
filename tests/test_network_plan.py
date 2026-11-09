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


def test_rounding_at_the_copper_limit_is_not_an_over_budget_pole():
    # far along a long fence the switch's arc no longer cancels exactly; the first feed is at the limit
    t = TerrainData(np.zeros((400, 400)), cell_size_m=5.0)
    long_plan = generate_perimeter_plan(t, [(100.0, 100.0), (1900.0, 100.0), (1900.0, 1900.0), (100.0, 1900.0)], CAM,
                                        target_ppm=40, mast_height_m=6, is_closed_loop=True, spacing_m=21.37)
    net = plan_network(long_plan, switch_ports=8, poe_budget_w=120, max_copper_m=90)
    assert net.warnings == [] and not any(s.over_budget for s in net.switches)
    assert net.longest_copper_m <= 90 + 1e-6


def test_storage_and_power_are_the_stated_formulas(plan):
    from cctv_simulator.network_plan import pse_w

    n = len(plan.placed_cameras)
    day = plan_network(plan, bitrate_fixed_mbps=4.0, retention_days=30, raid_overhead=0.15, night_factor=1.0)
    assert day.bitrate_mbps == pytest.approx(4.0 * n) and day.peak_bitrate_mbps == pytest.approx(4.0 * n)
    assert day.storage_tb == pytest.approx(4.0 * n * 1e6 / 8 * 86400 * 30 / 1e12 * 1.15)
    # visible cameras under IR: x2 for half the day -> storage on the average, NVR input on the peak
    net = plan_network(plan, bitrate_fixed_mbps=4.0, retention_days=30, raid_overhead=0.15)
    assert net.peak_bitrate_mbps == pytest.approx(8.0 * n)
    assert net.storage_tb == pytest.approx(6.0 * n * 1e6 / 8 * 86400 * 30 / 1e12 * 1.15)
    # PoE is budgeted at the PSE: 12 W device + 10 % cable loss -> class 15.4 W
    assert pse_w(12.0) == 15.4 and pse_w(60.0) == 75.0
    assert net.poe_total_w == pytest.approx(15.4 * n)
    assert camera_power_w({"power_consumption": "≤ 12,5 W"}) == (12.5, True)
    assert camera_power_w({}, ptz=True) == (60.0, False)                  # IR + heater + motors


def test_bill_names_every_tender_item_and_never_guesses_a_price(plan):
    from cctv_simulator.network_plan import PriceBook, estimate_cost

    net = plan_network(plan, autonomy_h=2.0, fibre_ring=True)
    assert net.trench_m > 0 and net.power_cable_m == net.trench_m and net.cabinets == len(net.switches)
    assert net.ups_wh == pytest.approx(net.ups_load_w * 2.0 / 0.85)
    chain = plan_network(plan)
    assert net.fibre_m > chain.fibre_m                                    # the ring's return path
    cost = estimate_cost(plan, net, PriceBook(camera=1000))
    items = {ln.item for ln in cost.lines}
    for want in ("Hendek (kazı + boru + dolgu)", "Enerji kablosu", "Saha kabini (switch + parafudr + enerji)",
                 "Topraklama + parafudr seti", "VMS / analitik lisansı", "Operatör iş istasyonu",
                 "Montaj aparatı", "Kamera montajı + devreye alma", "Bakım (1 yıl)"):
        assert want in items and want in cost.unpriced
    assert "Sabit kamera" not in cost.unpriced


def test_cost_names_what_is_not_priced(plan):
    net = plan_network(plan)
    c = estimate_cost(plan, net, PriceBook(camera=10_000, pole=5_000))
    n = len(plan.placed_cameras)
    assert c.total == pytest.approx(10_000 * n + 5_000 * plan.pole_count)
    assert "PoE switch (dış ortam)" in c.unpriced and "Sabit kamera" not in c.unpriced
