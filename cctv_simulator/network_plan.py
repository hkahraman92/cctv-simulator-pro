"""Cabling and network plan of a perimeter layout, and its bill of quantities.

Model (the usual perimeter practice, stated so the numbers can be checked):

* Every pole is cabled to an outdoor PoE switch along the fence. Copper run =
  distance along the fence to the switch + mast height + ``slack_frac``;
  Ethernet's 100 m limit is respected with a margin (``max_copper_m``, 90 m).
* Switches are placed greedily along the fence (arc length), each as far
  along as the copper limit allows, so the count is minimal for that rule.
  A switch also closes when its ports or PoE budget are full.
* Switches are chained to the NVR / control room by fibre along the fence
  (``nvr_at_m``: arc position of the control room, default the fence start).
* NVR: aggregate recording bitrate (planner's per-camera estimate) and
  storage for ``retention_days`` (+ RAID overhead).

Power per camera: the library's ``power_consumption`` when it holds a number
of watts, else a stated default (fixed 12 W; PTZ 60 W -- IR + heater + motors,
PoE++ class; IR adds nothing more to a datasheet figure). A switch's PoE
budget is spent at the PSE side: device watts + cable loss, rounded up to the
802.3at/bt class the port must grant.

Beyond cable and NVR the bill lists what a tender needs and the plan can
count: trench (one route for data + power), mains cable, field cabinets,
UPS (load x autonomy), grounding / surge protection per pole and cabinet,
VMS / analytics licences, operator workstations, mounting brackets,
installation labour and a year of maintenance. Quantities are the model's;
every unpriced line stays in the list and is named (nothing is guessed).
Recording: visible cameras add noise under IR at night (``night_factor``
for ``night_share`` of the day) -- storage uses the day/night average, the
NVR input the night peak. No Tk.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

DEFAULT_FIXED_W = 12.0
DEFAULT_PTZ_W = 60.0            # IR + heater + pan/tilt motors (PoE++); 30 W undersized a real PTZ
# PSE-side power a port must grant per 802.3af/at/bt class (W)
PSE_CLASSES_W = (4.0, 7.0, 15.4, 30.0, 45.0, 60.0, 75.0, 90.0)
CABLE_LOSS = 1.10               # ~10 % lost in 90 m of Cat6 at PoE++ currents
SWITCH_OWN_W = 30.0             # an outdoor switch's own draw (fans, heater) for the UPS
NVR_W = 300.0
CAMS_PER_WORKSTATION = 64       # 4 monitors x 16 tiles


def pse_w(device_w: float) -> float:
    """Power the switch port must budget for a device drawing ``device_w``."""
    need = device_w * CABLE_LOSS
    return next((c for c in PSE_CLASSES_W if c >= need - 1e-9), need)


@dataclass
class Switch:
    index: int
    x_m: float
    y_m: float
    arc_m: float                      # position along the fence
    poles: List[int] = field(default_factory=list)       # pole ids
    ports_used: int = 0
    poe_w: float = 0.0
    copper_m: float = 0.0
    over_budget: bool = False
    # a cabinet on the pole itself, fed by fibre from the line: an area plan's
    # interior pole too far from the edge for copper (never on a fence plan)
    on_pole: bool = False


@dataclass
class Feed:
    """One camera's cable: which switch port it lands on and what that port must grant."""
    ptz: bool
    cam_index: int                    # index in plan.ptz_cameras if ptz, else plan.placed_cameras
    pole_id: int
    switch_index: int
    port: int
    cable_m: float
    device_w: float
    port_w: float


@dataclass
class NetworkPlan:
    switches: List[Switch]
    copper_m: float                   # total camera cable
    fibre_m: float                    # switch chain to the NVR
    longest_copper_m: float
    cameras: int
    bitrate_mbps: float
    storage_tb: float
    retention_days: int
    poe_total_w: float
    warnings: List[str] = field(default_factory=list)
    assumptions: List[str] = field(default_factory=list)
    peak_bitrate_mbps: float = 0.0          # night (IR noise): what the NVR input must take
    trench_m: float = 0.0                   # one trench for data + power along the route
    power_cable_m: float = 0.0              # mains to the field cabinets
    cabinets: int = 0                       # one per switch (incl. pole-top)
    ups_load_w: float = 0.0
    ups_wh: float = 0.0                     # load x autonomy / inverter efficiency
    autonomy_h: float = 0.0
    poles: int = 0
    fibre_ring: bool = False
    feeds: List[Feed] = field(default_factory=list)     # per camera: switch, port, cable, power


def camera_power_w(model: Optional[Dict], ptz: bool = False) -> Tuple[float, bool]:
    """(watts, from_datasheet). Reads '25W', '≤ 12,5 W', 'Max 30 W' ..."""
    raw = str((model or {}).get("power_consumption", "") or "")
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*W\b", raw, re.IGNORECASE)
    if m:
        return float(m.group(1).replace(",", ".")), True
    return (DEFAULT_PTZ_W if ptz else DEFAULT_FIXED_W), False


def _arc_of(points: Sequence[Tuple[float, float]], x: float, y: float) -> float:
    return _arc_and_offset(points, x, y)[0]


def _arc_and_offset(points: Sequence[Tuple[float, float]], x: float, y: float) -> Tuple[float, float]:
    """(distance along the line of the nearest point, distance from the line)."""
    best, at, run = None, 0.0, 0.0
    for a, b in zip(points[:-1], points[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        seg = math.hypot(dx, dy)
        if seg < 1e-9:
            continue
        u = max(0.0, min(1.0, ((x - a[0]) * dx + (y - a[1]) * dy) / (seg * seg)))
        d = math.hypot(a[0] + u * dx - x, a[1] + u * dy - y)
        if best is None or d < best:
            best, at = d, run + u * seg
        run += seg
    return at, (best or 0.0)


def _lateral(points, cam) -> float:
    """Trench from the line to a pole standing off it (an area plan's interior
    pole); poles on the line (every fence plan) get 0, so their runs are unchanged."""
    d = _arc_and_offset(points, cam.x_m, cam.y_m)[1]
    return d if d > 0.5 else 0.0


def storage_tb(bitrate_mbps: float, retention_days: float = 30.0, raid_overhead: float = 0.15) -> float:
    """Recording storage in decimal TB (10^12 B), RAID overhead included. The
    one formula for the plan summary and the network plan (they used to
    differ: binary 32.0 vs decimal 32.8 TB for the same 88 Mbps)."""
    return bitrate_mbps * 1e6 / 8 * 86400 * retention_days / 1e12 * (1.0 + raid_overhead)


def plan_network(plan, *, camera_model: Optional[Dict] = None, ptz_model: Optional[Dict] = None,
                 bitrate_fixed_mbps: float = 4.0, bitrate_ptz_mbps: float = 4.0,
                 max_copper_m: float = 90.0, slack_frac: float = 0.10, switch_ports: int = 8,
                 poe_budget_w: float = 120.0, nvr_at_m: float = 0.0, retention_days: int = 30,
                 raid_overhead: float = 0.15, night_factor: float = 2.0, night_share: float = 0.5,
                 autonomy_h: float = 1.0, ups_efficiency: float = 0.85,
                 fibre_ring: bool = False) -> NetworkPlan:
    """Switches, cable and NVR sizing for a PerimeterPlanResult."""
    from .perimeter_planner import point_along_polyline

    pts = [tuple(p) for p in plan.fence_points]
    fixed_dev, fixed_ds = camera_power_w(camera_model)
    ptz_dev, ptz_ds = camera_power_w(ptz_model, ptz=True)
    fixed_w, ptz_w = pse_w(fixed_dev), pse_w(ptz_dev)          # what each port must grant
    # one feed per camera; PTZs share the pole with a fixed camera but need their own port
    feeds = [(c, False) for c in plan.placed_cameras] + [(c, True) for c in getattr(plan, "ptz_cameras", [])]
    which = {id(c): (False, i) for i, c in enumerate(plan.placed_cameras)}
    which.update({id(c): (True, i) for i, c in enumerate(getattr(plan, "ptz_cameras", []))})
    per_camera: List[Feed] = []

    def record(cam, is_ptz, sw, run):
        per_camera.append(Feed(ptz=is_ptz, cam_index=which[id(cam)][1], pole_id=cam.pole_id, switch_index=sw.index,
                               port=sw.ports_used, cable_m=run, device_w=ptz_dev if is_ptz else fixed_dev,
                               port_w=ptz_w if is_ptz else fixed_w))

    feeds.sort(key=lambda f: _arc_of(pts, f[0].x_m, f[0].y_m))
    warnings: List[str] = []
    switches: List[Switch] = []
    reach = max_copper_m / (1.0 + slack_frac)
    lateral_any = False
    # poles whose trench alone uses up the copper get their own cabinet (fibre to the line)
    remote = [f for f in feeds if _lateral(pts, f[0]) + f[0].mast_height_m > reach]
    feeds = [f for f in feeds if _lateral(pts, f[0]) + f[0].mast_height_m <= reach]
    lateral_fibre = 0.0

    i = 0
    while i < len(feeds):
        first = _arc_of(pts, feeds[i][0].x_m, feeds[i][0].y_m)
        mast = feeds[i][0].mast_height_m + _lateral(pts, feeds[i][0])
        # switch as far along as the first feed's copper allows
        at = first + max(reach - mast, 0.0)
        sx, sy = point_along_polyline(pts, at)[:2]
        sw = Switch(index=len(switches) + 1, x_m=float(sx), y_m=float(sy), arc_m=at)
        while i < len(feeds):
            cam, is_ptz = feeds[i]
            arc = _arc_of(pts, cam.x_m, cam.y_m)
            off = _lateral(pts, cam)
            lateral_any = lateral_any or off > 0
            run = (abs(arc - at) + off + cam.mast_height_m) * (1.0 + slack_frac)
            w = ptz_w if is_ptz else fixed_w
            # the first feed sits exactly at the copper limit by construction; arc rounding must not trip it
            if run > max_copper_m + 1e-6 or sw.ports_used + 1 > switch_ports or sw.poe_w + w > poe_budget_w:
                if sw.ports_used == 0:          # a single feed that cannot fit anywhere
                    sw.over_budget = True
                    warnings.append(f"Direk {cam.pole_id}: tek başına PoE bütçesini ya da kablo sınırını aşıyor.")
                else:
                    break
            sw.poles.append(cam.pole_id)
            sw.ports_used += 1
            sw.poe_w += w
            sw.copper_m += run
            record(cam, is_ptz, sw, run)
            i += 1
        switches.append(sw)

    by_pole: Dict[Tuple[float, float], List] = {}
    for f in remote:
        by_pole.setdefault((f[0].x_m, f[0].y_m), []).append(f)
    for (x, y), group in by_pole.items():
        arc = _arc_of(pts, x, y)
        sw = None
        for cam, is_ptz in group:
            w = ptz_w if is_ptz else fixed_w
            if sw is None or sw.ports_used + 1 > switch_ports or sw.poe_w + w > poe_budget_w:
                sw = Switch(index=len(switches) + 1, x_m=float(x), y_m=float(y), arc_m=arc, on_pole=True)
                switches.append(sw)
                lateral_fibre += _lateral(pts, cam) * (1.0 + slack_frac)
            sw.poles.append(cam.pole_id)
            sw.ports_used += 1
            sw.poe_w += w
            sw.copper_m += cam.mast_height_m * (1.0 + slack_frac)
            record(cam, is_ptz, sw, cam.mast_height_m * (1.0 + slack_frac))
        feeds += group

    copper = sum(s.copper_m for s in switches)
    longest = 0.0
    for s in switches:
        if s.ports_used:
            longest = max(longest, s.copper_m / s.ports_used)
    fibre = 0.0
    span = 0.0
    if switches:
        arcs = sorted(s.arc_m for s in switches)
        span = max(arcs[-1], nvr_at_m) - min(arcs[0], nvr_at_m)
        fibre = span * (1.0 + slack_frac) + lateral_fibre
    line_len = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts[:-1], pts[1:]))
    closed = len(pts) > 3 and pts[0] == pts[-1]
    if fibre_ring and switches:
        # return path: round the rest of a closed fence, else back along the same trench
        fibre += (max(line_len - span, 0.0) if closed else span) * (1.0 + slack_frac)
    # visible cameras record IR noise at night; thermal ones do not
    def _visible(c):
        return "LWIR" not in c.sensor_name.upper() and "MWIR" not in c.sensor_name.upper()
    fixed_vis = sum(1 for c in plan.placed_cameras if _visible(c))
    ptz_vis = sum(1 for c in getattr(plan, "ptz_cameras", []) if _visible(c))
    bitrate = (len(plan.placed_cameras) * bitrate_fixed_mbps
               + len(getattr(plan, "ptz_cameras", [])) * bitrate_ptz_mbps)
    extra_night = (fixed_vis * bitrate_fixed_mbps + ptz_vis * bitrate_ptz_mbps) * (night_factor - 1.0)
    peak = bitrate + extra_night
    storage = storage_tb(bitrate + extra_night * night_share, retention_days, raid_overhead)
    # one trench carries data + power: the covered stretch of the line (+ the
    # route to the NVR) and every lateral to a pole off the line
    feed_arcs = [_arc_of(pts, c.x_m, c.y_m) for c, _ in feeds] + [s.arc_m for s in switches] + [nvr_at_m]
    laterals = sum(_lateral(pts, c) for c, _ in feeds if not _)
    trench = ((max(feed_arcs) - min(feed_arcs)) if feed_arcs else 0.0) + laterals
    poles = len({c.pole_id for c, _ in feeds})
    load = sum(s.poe_w for s in switches) / CABLE_LOSS + SWITCH_OWN_W * len(switches) + NVR_W
    ups_wh = load * autonomy_h / max(ups_efficiency, 0.1)
    assumptions = [
        f"Bakır kablo: çit boyunca + direk boyu + %{slack_frac * 100:.0f} pay, en çok {max_copper_m:.0f} m."
        + (" Hat dışındaki direklere sınırdan dik hendek eklenir." if lateral_any or remote else "")
        + (f" Sınıra bakırla ulaşamayan {len(by_pole)} direğe direk üstü switch + fiber." if remote else ""),
        f"Switch: {switch_ports} port, PoE bütçesi {poe_budget_w:.0f} W.",
        f"Kamera gücü: sabit {fixed_dev:g} W ({'broşür' if fixed_ds else 'varsayılan'}; port {fixed_w:g} W)"
        + (f", PTZ {ptz_dev:g} W ({'broşür' if ptz_ds else 'varsayılan'}; port {ptz_w:g} W)"
           if getattr(plan, "ptz_cameras", []) else "") + ".",
        f"Kayıt: {retention_days} gün, RAID payı %{raid_overhead * 100:.0f}; görünür kameralar gece IR "
        f"gürültüsüyle ×{night_factor:g} bit hızı (günün %{night_share * 100:.0f}'si) — depolama ortalamayla, "
        f"NVR girişi gece tepesiyle ({peak:.0f} Mbps).",
        f"PoE: port bütçesi cihaz gücü × {CABLE_LOSS:g} (kablo kaybı), 802.3af/at/bt sınıfına yuvarlanır.",
        f"UPS: kameralar + switch ({SWITCH_OWN_W:g} W) + NVR ({NVR_W:g} W) = {load:.0f} W, "
        f"{autonomy_h:g} saat otonomi, verim %{ups_efficiency * 100:.0f} → {ups_wh:.0f} Wh.",
        "Hendek: veri ve enerji aynı güzergâhta; enerji kablosu her kabine aynı hendekten.",
        "Fiber: " + ("halka (çift yol)." if fibre_ring else "zincir — bir switch arızası arkasındakileri koparır; halka önerilir."),
    ]
    return NetworkPlan(switches=switches, copper_m=copper, fibre_m=fibre, longest_copper_m=longest,
                       cameras=len(feeds), bitrate_mbps=bitrate, storage_tb=storage,
                       retention_days=retention_days, poe_total_w=sum(s.poe_w for s in switches),
                       warnings=warnings, assumptions=assumptions, peak_bitrate_mbps=peak,
                       trench_m=trench * (1.0 + slack_frac), power_cable_m=trench * (1.0 + slack_frac),
                       cabinets=len(switches), ups_load_w=load, ups_wh=ups_wh, autonomy_h=autonomy_h,
                       poles=poles, fibre_ring=fibre_ring, feeds=per_camera)


@dataclass
class PriceBook:
    """Unit prices (user-entered; 0 = not priced, reported as such)."""
    camera: float = 0.0
    ptz: float = 0.0
    pole: float = 0.0                 # pole + foundation + installation
    switch: float = 0.0
    copper_per_m: float = 0.0
    fibre_per_m: float = 0.0
    nvr_per_tb: float = 0.0
    nvr_base: float = 0.0
    trench_per_m: float = 0.0         # excavation + backfill + duct
    power_cable_per_m: float = 0.0
    cabinet: float = 0.0              # field cabinet (switch, surge, power)
    ups_per_kwh: float = 0.0
    grounding_set: float = 0.0        # ground rod + data / power surge protection, per pole and cabinet
    vms_licence: float = 0.0          # per camera (VMS + analytics)
    workstation: float = 0.0          # operator workstation incl. monitors
    bracket: float = 0.0              # mounting bracket / pole adapter, per camera
    install_per_camera: float = 0.0   # mounting, cabling, commissioning
    maintenance_per_camera_year: float = 0.0
    currency: str = "TL"


@dataclass
class CostLine:
    item: str
    qty: float
    unit: str
    unit_price: float
    total: float


@dataclass
class CostEstimate:
    lines: List[CostLine]
    total: float
    unpriced: List[str]
    currency: str


def estimate_cost(plan, net: NetworkPlan, prices: PriceBook) -> CostEstimate:
    """Bill of quantities x prices. Unpriced items stay in the list with 0 and are named."""
    n_fixed = len(plan.placed_cameras)
    n_ptz = len(getattr(plan, "ptz_cameras", []))
    rows = [
        ("Sabit kamera", n_fixed, "adet", prices.camera),
        ("PTZ kamera", n_ptz, "adet", prices.ptz),
        ("Direk (temel + montaj dahil)", plan.pole_count, "adet", prices.pole),
        ("PoE switch (dış ortam)", len(net.switches), "adet", prices.switch),
        ("Bakır kablo (Cat6 dış ortam)", round(net.copper_m), "m", prices.copper_per_m),
        ("Fiber omurga", round(net.fibre_m), "m", prices.fibre_per_m),
        ("NVR kayıt kapasitesi", round(net.storage_tb, 1), "TB", prices.nvr_per_tb),
        ("NVR / sunucu (sabit)", 1 if (n_fixed + n_ptz) else 0, "adet", prices.nvr_base),
        ("Hendek (kazı + boru + dolgu)", round(net.trench_m), "m", prices.trench_per_m),
        ("Enerji kablosu", round(net.power_cable_m), "m", prices.power_cable_per_m),
        ("Saha kabini (switch + parafudr + enerji)", net.cabinets, "adet", prices.cabinet),
        (f"UPS ({net.autonomy_h:g} sa otonomi)", round(net.ups_wh / 1000.0, 2), "kWh", prices.ups_per_kwh),
        ("Topraklama + parafudr seti", (net.poles or plan.pole_count) + net.cabinets, "set", prices.grounding_set),
        ("VMS / analitik lisansı", n_fixed + n_ptz, "kamera", prices.vms_licence),
        ("Operatör iş istasyonu", math.ceil((n_fixed + n_ptz) / CAMS_PER_WORKSTATION) if (n_fixed + n_ptz) else 0,
         "adet", prices.workstation),
        ("Montaj aparatı", n_fixed + n_ptz, "adet", prices.bracket),
        ("Kamera montajı + devreye alma", n_fixed + n_ptz, "kamera", prices.install_per_camera),
        ("Bakım (1 yıl)", n_fixed + n_ptz, "kamera·yıl", prices.maintenance_per_camera_year),
    ]
    lines = [CostLine(item, qty, unit, price, round(qty * price, 2)) for item, qty, unit, price in rows if qty]
    unpriced = [ln.item for ln in lines if ln.unit_price <= 0]
    return CostEstimate(lines=lines, total=round(sum(ln.total for ln in lines), 2), unpriced=unpriced,
                        currency=prices.currency)
