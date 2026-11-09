"""Which camera model holds the fence goal with the fewest poles / at the lowest cost?

For every candidate model: the pole search (``pole_optimizer.minimize_poles``,
per-pole focal / tilt and optionally place), the winning plan with its
lenses applied, its network plan and its cost. Ranking: models that meet the
goal first, then by total cost when every candidate is priced, else by pole
count. No Tk.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .models import CameraConfig
from .network_plan import CostEstimate, NetworkPlan, PriceBook, estimate_cost, plan_network
from .terrain_loader import TerrainData


@dataclass
class ModelOption:
    model_name: str
    met: bool
    poles: int
    band_frac: float
    plan: object = None               # PerimeterPlanResult (lenses applied) or None
    network: Optional[NetworkPlan] = None
    cost: Optional[CostEstimate] = None
    seconds: float = 0.0
    notes: List[str] = field(default_factory=list)


def compare_models(terrain: TerrainData, fence_points: Sequence[Tuple[float, float]],
                   candidates: Sequence[Tuple[str, CameraConfig, Dict]], *, plan_kwargs: Dict,
                   goal_frac: float = 0.95, level: int, band_m: float = 40.0,
                   inside_m: Optional[float] = None, position_steps: int = 0,
                   prices: Optional[Dict[str, float]] = None, price_book: Optional[PriceBook] = None,
                   progress: Optional[Callable[[int, int, str], None]] = None) -> List[ModelOption]:
    """``prices``: per-model camera unit price (overrides ``price_book.camera``)."""
    import time

    from .focal_optimizer import apply_layout
    from .perimeter_planner import _bitrate_mbps
    from .pole_optimizer import minimize_poles

    book = price_book or PriceBook()
    out: List[ModelOption] = []
    for k, (name, cam, record) in enumerate(candidates):
        if progress:
            progress(k, len(candidates), name)
        t0 = time.perf_counter()
        try:
            res = minimize_poles(terrain, list(fence_points), cam, plan_kwargs=plan_kwargs, goal_frac=goal_frac,
                                 level=level, band_m=band_m, inside_m=inside_m, position_steps=position_steps)
        except ValueError as exc:            # this model cannot hold the target at all
            out.append(ModelOption(model_name=name, met=False, poles=0, band_frac=0.0,
                                   seconds=time.perf_counter() - t0, notes=[str(exc)]))
            continue
        best = res.best
        plan = res.best_plan
        if plan is not None and res.best_layout is not None:
            plan = apply_layout(plan, res.best_layout, cam, plan_kwargs.get("target_ppm", 40.0), terrain=terrain,
                                visibility_km=plan_kwargs.get("visibility_km", 40.0),
                                weather=plan_kwargs.get("weather", ""))
        net = cost = None
        if plan is not None and plan.placed_cameras:
            net = plan_network(plan, camera_model=record, bitrate_fixed_mbps=_bitrate_mbps(cam))
            mb = PriceBook(**{**book.__dict__, "camera": (prices or {}).get(name, book.camera)})
            cost = estimate_cost(plan, net, mb)
        out.append(ModelOption(model_name=name, met=res.met, poles=best.fixed_poles if best else 0,
                               band_frac=best.optimised_frac if best else 0.0, plan=plan, network=net, cost=cost,
                               seconds=time.perf_counter() - t0, notes=list(res.notes)))
    # a bool, not the list itself: list.sort empties the list while the key runs
    priced = bool(out) and all(o.cost is not None and not o.cost.unpriced for o in out)
    out.sort(key=lambda o: (not o.met, (o.cost.total if priced and o.cost else 0.0), o.poles, -o.band_frac))
    if progress:
        progress(len(candidates), len(candidates), "")
    return out
