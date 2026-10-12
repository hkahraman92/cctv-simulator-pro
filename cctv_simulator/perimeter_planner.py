"""
Multi-Camera Perimeter Auto-Planner & Fence Line Optimization Engine.

Performs:
1. Interactive polyline/polygon fence perimeter parsing and distance measurement.
2. DORI-compliant optimal pole spacing calculation with dead-zone overlapping.
3. Automatic placement of 10 to 100+ cameras along facility boundary fences.
4. Corner and terrain slope adaptation (avoiding corner blind spots).
5. Bill of Materials (BOM), storage, and bandwidth estimation.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from . import atmosphere as _atm
from .terrain_loader import TerrainData
from .models import CameraConfig
from .calculations import CameraOptics, camera_optics
from .viewshed_3d import PPM_DETECT, PPM_IDENT, PPM_OBSERVE, PPM_RECOG


@dataclass
class PlacedCamera:
    """Individual camera placed along the perimeter."""
    pole_id: int
    x_m: float
    y_m: float
    ground_z_m: float
    mast_height_m: float
    pan_deg: float
    tilt_deg: float
    focal_mm: float
    hfov_deg: float
    vfov_deg: float
    effective_range_m: float
    dead_zone_m: float
    camera_model: str
    sensor_name: str
    resolution_name: str
    # PTZ rows only: pan_deg is the patrol centre, patrol_span_deg the swept
    # width (360 = continuous rotation); 0 for a fixed camera.
    patrol_span_deg: float = 0.0

    @property
    def total_z_m(self) -> float:
        return self.ground_z_m + self.mast_height_m


@dataclass
class FenceGap:
    """Uncovered segment along the fence line."""
    start_x: float
    start_y: float
    end_x: float
    end_y: float
    length_m: float
    reason: str = "Aralık yetersizliği veya engel"


@dataclass
class PerimeterPlanResult:
    """Complete Perimeter Surveillance Layout Output."""
    fence_points: List[Tuple[float, float]]
    total_fence_length_m: float
    segment_lengths_m: List[float]
    placed_cameras: List[PlacedCamera]
    gaps: List[FenceGap] = field(default_factory=list)

    # BOM & Engineering Summary
    camera_count: int = 0
    pole_count: int = 0
    target_ppm: float = 40.0
    avg_spacing_m: float = 0.0
    coverage_percentage: float = 100.0
    estimated_bandwidth_mbps: float = 0.0
    estimated_storage_30days_tb: float = 0.0
    line_mode: str = "facility"      # "facility" | "border" | "highway"
    watch_side: str = "right"        # only meaningful for line_mode == "border"
    # Mixed layout: PTZs co-mounted on every Nth fixed pole. Kept apart from
    # placed_cameras on purpose -- fence-gap analysis, the coverage heatmap,
    # glare and the BOM rows describe the CONTINUOUS (fixed) coverage; a PTZ
    # only watches a spot part of the time (perimeter_ptz evaluates it).
    ptz_cameras: List[PlacedCamera] = field(default_factory=list)

    @property
    def ptz_count(self) -> int:
        return len(self.ptz_cameras)


def point_along_polyline(points: List[Tuple[float, float]],
                         dist_m: float) -> Tuple[float, float, int]:
    """World point at ``dist_m`` measured along the fence polyline.

    Returns ``(x, y, segment_index)``. ``dist_m`` is clamped to
    ``[0, total_length]``. Used to map a cross-section-profile mouse position
    back to a location on the map.
    """
    if not points:
        return 0.0, 0.0, 0
    if len(points) == 1:
        return points[0][0], points[0][1], 0

    if dist_m <= 0.0:
        return points[0][0], points[0][1], 0

    walked = 0.0
    for i in range(len(points) - 1):
        (x0, y0), (x1, y1) = points[i], points[i + 1]
        seg = math.hypot(x1 - x0, y1 - y0)
        if seg <= 1e-9:
            continue
        if walked + seg >= dist_m:
            t = (dist_m - walked) / seg
            return x0 + t * (x1 - x0), y0 + t * (y1 - y0), i
        walked += seg

    return points[-1][0], points[-1][1], len(points) - 2


def calculate_optimal_spacing(camera: CameraConfig,
                              target_ppm: float = 40.0,
                              mast_height_m: float = 5.0,
                              overlap_pct: float = 0.15,
                              lens_mode: str = "min",
                              visibility_km: float = 40.0,
                              weather: str = "") -> Tuple[float, float, float]:
    """Calculates effective range, dead zone, and recommended pole spacing (meters)."""
    focal_mm = camera.focal_min_mm if lens_mode == "min" else camera.focal_max_mm
    optics = camera_optics(camera, focal_mm)      # Kural 1: one optics resolver

    # Optical slant range where resolution equals target PPM (EN 62676-4)
    max_slant_dist = optics.distance_for_ppm(max(target_ppm, 1.0))
    # Fog / rain / haze cap the range where target contrast survives the path.
    band = _atm.band_for_camera(camera.sensor_name, camera.model_name)
    max_slant_dist = _atm.usable_range_m(max_slant_dist, visibility_km, band, weather)
    # Ground reach (Pythagoras with mast height)
    ground_reach = math.sqrt(max(max_slant_dist**2 - mast_height_m**2, 100.0))

    # Dead zone under the pole. BUGFIX: this used to assume a flat 15 deg
    # downward tilt regardless of lens/mast, while generate_perimeter_plan
    # actually points the camera at ~55% of ground_reach (near-flat for a
    # tele lens, steep for a wide lens) -- the two disagreed on the very tilt
    # the placed camera uses, so the BOM's dead-zone/spacing figures didn't
    # match the cameras it went on to place. Mirror the same aim convention
    # here (the dead_zone*2.0 floor term generate_perimeter_plan also applies
    # isn't known yet at this point and is a rare-case clamp, not the driver).
    vfov_deg = optics.vfov_deg
    aim_dist = max(ground_reach * 0.55, 5.0)
    tilt_deg = max(1.5, min(25.0, math.degrees(math.atan(mast_height_m / aim_dist))))
    top_ray_angle_deg = tilt_deg + (vfov_deg / 2.0)
    bottom_ray_angle_deg = tilt_deg - (vfov_deg / 2.0)

    # Distance to where bottom of picture touches the ground
    if bottom_ray_angle_deg > 2.0:
        dead_zone_m = mast_height_m / math.tan(math.radians(bottom_ray_angle_deg))
    else:
        dead_zone_m = mast_height_m * 1.2

    dead_zone_m = max(1.5, min(dead_zone_m, ground_reach * 0.25))

    # Effective inter-pole spacing with safety overlap
    raw_span = max(ground_reach - dead_zone_m, 10.0)
    optimal_spacing_m = raw_span * (1.0 - overlap_pct)

    return ground_reach, dead_zone_m, optimal_spacing_m


def generate_perimeter_plan(terrain: TerrainData,
                            fence_points: List[Tuple[float, float]],
                            camera: CameraConfig,
                            target_ppm: float = 40.0,
                            overlap_pct: float = 0.15,
                            mast_height_m: float = 5.0,
                            lens_mode: str = "min",
                            is_closed_loop: bool = True,
                            visibility_km: float = 40.0,
                            weather: str = "",
                            line_mode: str = "facility",
                            watch_side: str = "right",
                            ptz_camera: Optional[CameraConfig] = None,
                            ptz_every_n: int = 0,
                            ptz_patrol_span_deg: float = 360.0,
                            ptz_target_ppm: float = PPM_IDENT) -> PerimeterPlanResult:
    """Places cameras along fence line ensuring continuous dead-zone overlapping coverage.

    ``line_mode`` picks what the poles actually watch — the three use cases
    need different camera orientations, not just a different polyline shape:

    - ``"facility"`` (default, original behaviour): a fence around a compound.
      Each pole looks *along* the fence at the next pole, the way you'd watch
      a fence line for climbing/cutting. Corner guards fire on sharp turns.
    - ``"border"``: a boundary line. There is no "inside" to ring, so poles
      instead look *perpendicular* to the line, across the zone beyond it —
      ``watch_side`` picks which side ("left"/"right" of the direction the
      polyline was drawn in).
    - ``"highway"``: a road/corridor. Poles look *back along* the line, i.e.
      against the direction the polyline was drawn in (ANPR-style — draw the
      line in the direction of travel you want the plates read from).

    Corner-guard cameras (the sharp-turn wedge fix) only make sense for the
    facility case and are skipped for "border"/"highway".

    Mixed layout: with ``ptz_camera`` and ``ptz_every_n >= 1`` a PTZ is
    co-mounted on every Nth regular pole (1st, N+1th, ...). Its patrol is
    centred on the side being watched -- the outward normal of the fence for
    "facility", the pole heading for "border"/"highway" -- and spans
    ``ptz_patrol_span_deg`` (360 = continuous rotation). The PTZ row's
    ``effective_range_m`` is its slew-to-cue reach: the ground distance where
    the longest focal still resolves ``ptz_target_ppm``.
    """
    if len(fence_points) < 2:
        return PerimeterPlanResult(
            fence_points=fence_points,
            total_fence_length_m=0.0,
            segment_lengths_m=[],
            placed_cameras=[],
        )

    # Build sequence of segments
    pts = list(fence_points)
    if is_closed_loop and (pts[0] != pts[-1]):
        pts.append(pts[0])

    # Calculate segment lengths
    segment_lengths = []
    total_len = 0.0
    for i in range(len(pts) - 1):
        seg_len = math.hypot(pts[i+1][0] - pts[i][0], pts[i+1][1] - pts[i][1])
        segment_lengths.append(seg_len)
        total_len += seg_len

    ground_reach, dead_zone, optimal_spacing = calculate_optimal_spacing(
        camera=camera,
        target_ppm=target_ppm,
        mast_height_m=mast_height_m,
        overlap_pct=overlap_pct,
        lens_mode=lens_mode,
        visibility_km=visibility_km,
        weather=weather,
    )

    focal_mm = camera.focal_min_mm if lens_mode == "min" else camera.focal_max_mm
    optics = camera_optics(camera, focal_mm)
    hfov_deg = optics.hfov_deg
    vfov_deg = optics.vfov_deg

    # Tilt: aim the optical axis at ~55 % of the ground reach so the vertical
    # frame actually lands on the fence line, instead of a fixed -14 deg that
    # only frames a few metres of ground with a tele lens. Clamped to a sane
    # surveillance band.
    aim_dist = max(ground_reach * 0.55, dead_zone * 2.0, 5.0)
    default_tilt = -max(1.5, min(25.0, math.degrees(math.atan(mast_height_m / aim_dist))))

    placed_cameras: List[PlacedCamera] = []
    pole_counter = 1

    def _place(px: float, py: float, pan_deg: float, *, reach: float = ground_reach,
               dz: float = dead_zone, tilt: float = default_tilt, note: str = "") -> None:
        nonlocal pole_counter
        gz = terrain.get_elevation_at(px, py)
        placed_cameras.append(PlacedCamera(
            pole_id=pole_counter,
            x_m=round(px, 1), y_m=round(py, 1), ground_z_m=round(gz, 1),
            mast_height_m=mast_height_m,
            pan_deg=round(pan_deg % 360.0, 1), tilt_deg=round(tilt, 1),
            focal_mm=focal_mm, hfov_deg=round(hfov_deg, 1), vfov_deg=round(vfov_deg, 1),
            effective_range_m=round(reach, 1), dead_zone_m=round(dz, 1),
            camera_model=camera.name + (f" [{note}]" if note else ""),
            sensor_name=camera.sensor_name, resolution_name=camera.resolution_name,
        ))
        pole_counter += 1

    def _pole_heading(seg_heading_deg: float) -> float:
        if line_mode == "border":
            # Facing the direction of travel (bearing seg_heading_deg), your
            # left hand points to bearing seg_heading_deg - 90, your right
            # hand to seg_heading_deg + 90 (e.g. walking east/90 deg: left
            # is north/0 deg, right is south/180 deg).
            offset = -90.0 if watch_side == "left" else 90.0
            return (seg_heading_deg + offset) % 360.0
        if line_mode == "highway":
            return (seg_heading_deg + 180.0) % 360.0
        return seg_heading_deg   # facility: look along the fence toward the next pole

    regular_poles: List[Tuple[PlacedCamera, float]] = []    # PTZ hosts
    seg_headings: List[float] = []
    for (p1, p2, seg_len) in zip(pts[:-1], pts[1:], segment_lengths):
        if seg_len < 1e-6:
            seg_headings.append(0.0)
            continue
        h = (math.degrees(math.atan2(p2[0] - p1[0], p2[1] - p1[1])) + 360.0) % 360.0
        seg_headings.append(h)

    # Walk along each segment: a pole at the start vertex + evenly spaced poles.
    for (p1, p2, seg_len), seg_heading in zip(zip(pts[:-1], pts[1:], segment_lengths), seg_headings):
        if seg_len < 1.0:
            continue
        dx, dy = p2[0] - p1[0], p2[1] - p1[1]
        pole_heading = _pole_heading(seg_heading)
        num_spans = max(1, int(math.ceil(seg_len / optimal_spacing)))
        for step_idx in range(num_spans):
            f = step_idx / num_spans
            _place(p1[0] + f * dx, p1[1] + f * dy, pole_heading)
            regular_poles.append((placed_cameras[-1], seg_heading))

    # Open perimeter: the far endpoint is not the start of any segment, so add a
    # pole there too. Facility mode looks back along the last leg (it's a dead
    # end of fence to watch); border/highway keep the same orientation rule as
    # every other pole on that leg.
    if not is_closed_loop and total_len > 1.0:
        end_heading = ((seg_headings[-1] + 180.0) % 360.0) if line_mode == "facility" \
            else _pole_heading(seg_headings[-1])
        _place(pts[-1][0], pts[-1][1], end_heading, note="uç")

    # Corner guards: at a sharp turn the vertex pole faces the *outgoing* leg but
    # its dead zone leaves the fence right at the corner uncovered, and the
    # incoming poles face away from the new leg. Add a wide-angle corner camera
    # on the vertex, aimed steeply down the outgoing leg so its dead zone shrinks
    # to ~1 m and the corner wedge is covered. Facility-only: a border/highway
    # bend doesn't create the same "wedge outside the enclosed area" blind spot,
    # since every pole already watches perpendicular/backward rather than the
    # fence itself.
    if line_mode == "facility":
        n_vert = len(pts) - 1
        corner_range = min(ground_reach, max(dead_zone * 6.0, 40.0))
        corner_dz = max(dead_zone * 0.15, 1.0)
        corner_tilt = -max(3.0, min(35.0, math.degrees(math.atan(mast_height_m / max(corner_range * 0.35, 3.0)))))
        interior = list(range(1, n_vert))
        if is_closed_loop:
            interior.append(0)                       # the closing corner (last leg -> first leg)
        for vi in interior:
            h_in = seg_headings[vi - 1]               # vi == 0 -> seg_headings[-1], the last leg
            h_out = seg_headings[vi % len(seg_headings)]
            turn = abs((h_out - h_in + 180.0) % 360.0 - 180.0)
            if turn < 20.0:
                continue
            vx, vy = pts[vi]
            _place(vx, vy, h_out, reach=corner_range, dz=corner_dz, tilt=corner_tilt, note="köşe")

    ptz_cams: List[PlacedCamera] = []
    if ptz_camera is not None and ptz_every_n >= 1 and regular_poles:
        ptz_cams = _place_ptz_cameras(
            pts, regular_poles[::int(ptz_every_n)], ptz_camera,
            line_mode=line_mode, is_closed_loop=is_closed_loop,
            span_deg=ptz_patrol_span_deg, target_ppm=ptz_target_ppm,
            visibility_km=visibility_km, weather=weather)

    # Coverage / gap analysis along the fence line (with DEM line-of-sight).
    gaps, coverage_pct = _analyse_fence_coverage(
        pts, placed_cameras, visibility_km, weather,
        _atm.band_for_camera(camera.sensor_name, camera.model_name),
        terrain=terrain,
    )

    # Compute BOM metrics
    num_cams = len(placed_cameras)
    avg_spacing = (total_len / max(num_cams, 1)) if num_cams > 0 else 0.0

    total_bandwidth_mbps = num_cams * _bitrate_mbps(camera)
    if ptz_cams:
        total_bandwidth_mbps += len(ptz_cams) * _bitrate_mbps(ptz_camera)
    # Storage for 30 days continuous recording (TB).
    # 1 Mbps = 1e6/8 B/s -> 10.8 GB/day -> 324 GB per Mbps over 30 days.
    storage_30days_tb = (total_bandwidth_mbps * 324.0 * 1.15) / 1024.0  # +15% RAID overhead

    return PerimeterPlanResult(
        fence_points=pts,
        total_fence_length_m=round(total_len, 1),
        segment_lengths_m=[round(s, 1) for s in segment_lengths],
        placed_cameras=placed_cameras,
        gaps=gaps,
        camera_count=num_cams + len(ptz_cams),
        pole_count=num_cams,
        target_ppm=target_ppm,
        avg_spacing_m=round(avg_spacing, 1),
        coverage_percentage=round(coverage_pct, 1),
        estimated_bandwidth_mbps=round(total_bandwidth_mbps, 1),
        estimated_storage_30days_tb=round(storage_30days_tb, 1),
        line_mode=line_mode,
        watch_side=watch_side,
        ptz_cameras=ptz_cams,
    )


def _bitrate_mbps(camera: CameraConfig) -> float:
    """Recording bitrate guess: ~4 Mbps per 4 MP H.265 stream, ~8 for 4K /
    8 MP, ~2.5 for thermal."""
    if "TERMAL" in camera.model_name.upper() or "LWIR" in camera.sensor_name.upper():
        return 2.5
    if "4K" in camera.resolution_name or "8 MP" in camera.resolution_name:
        return 8.0
    return 4.0


def _outward_normal_deg(seg_heading_deg: float, pts: List[Tuple[float, float]],
                        at_xy: Tuple[float, float], is_closed_loop: bool) -> float:
    """Bearing perpendicular to the fence that points AWAY from the enclosed
    area. Closed ring: the winding decides (a counter-clockwise ring has its
    interior on the left of travel). Open line: away from the centroid."""
    right, left = (seg_heading_deg + 90.0) % 360.0, (seg_heading_deg - 90.0) % 360.0
    if is_closed_loop and len(pts) >= 4:
        area2 = sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(pts[:-1], pts[1:]))
        if abs(area2) > 1e-6:
            return right if area2 > 0 else left
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    away = math.degrees(math.atan2(at_xy[0] - cx, at_xy[1] - cy))
    d_right = abs((right - away + 180.0) % 360.0 - 180.0)
    d_left = abs((left - away + 180.0) % 360.0 - 180.0)
    return right if d_right <= d_left else left


def _place_ptz_cameras(pts: List[Tuple[float, float]],
                       hosts: List[Tuple[PlacedCamera, float]], ptz: CameraConfig, *,
                       line_mode: str, is_closed_loop: bool, span_deg: float,
                       target_ppm: float, visibility_km: float,
                       weather: str) -> List[PlacedCamera]:
    """One PTZ per host pole, same pole id and mast (co-mounted)."""
    span = float(min(max(span_deg, 10.0), 360.0))
    wide = camera_optics(ptz, ptz.focal_min_mm)
    tele = camera_optics(ptz, ptz.focal_max_mm)
    band = _atm.band_for_camera(ptz.sensor_name, ptz.model_name)
    out: List[PlacedCamera] = []
    for host, seg_heading in hosts:
        mast = host.mast_height_m
        if line_mode == "facility":
            centre = _outward_normal_deg(seg_heading, pts, (host.x_m, host.y_m), is_closed_loop)
        else:
            centre = host.pan_deg      # border: the watched side; highway: back along the road
        # Patrol tilt: aim at ~55 % of the wide view's detection range, the
        # same convention the fixed poles use.
        det = _atm.usable_range_m(wide.distance_for_ppm(PPM_DETECT), visibility_km, band, weather)
        det_ground = math.sqrt(max(det ** 2 - mast ** 2, 1.0))
        tilt = -max(1.0, min(25.0, math.degrees(math.atan(mast / max(det_ground * 0.55, 5.0)))))
        cue = _atm.usable_range_m(tele.distance_for_ppm(max(target_ppm, 1.0)),
                                  visibility_km, band, weather)
        cue_ground = math.sqrt(max(cue ** 2 - mast ** 2, 1.0))
        out.append(PlacedCamera(
            pole_id=host.pole_id, x_m=host.x_m, y_m=host.y_m, ground_z_m=host.ground_z_m,
            mast_height_m=mast, pan_deg=round(centre % 360.0, 1), tilt_deg=round(tilt, 1),
            focal_mm=ptz.focal_min_mm, hfov_deg=round(wide.hfov_deg, 1),
            vfov_deg=round(wide.vfov_deg, 1), effective_range_m=round(cue_ground, 1),
            dead_zone_m=0.0, camera_model=ptz.name + " [PTZ]",
            sensor_name=ptz.sensor_name, resolution_name=ptz.resolution_name,
            patrol_span_deg=span,
        ))
    return out


def _analyse_fence_coverage(pts: List[Tuple[float, float]],
                            cams: List[PlacedCamera],
                            visibility_km: float, weather: str, band: str,
                            terrain: Optional[TerrainData] = None,
                            sample_step_m: float = 2.0,
                            min_gap_m: float = 4.0) -> Tuple[List[FenceGap], float]:
    """Walk the fence polyline; a point is covered when some camera has it inside
    its horizontal cone, past the dead zone, within the (weather-capped) range
    and — when a ``terrain`` is given — with a clear DEM line of sight. Returns
    the gap list and the covered percentage.

    PERF: this used to be a pure-Python point x camera nested loop (each pair
    re-deriving distance/azimuth/elevation and, when occluded, a 12-sample DEM
    line of sight) -- for a long fence with many placed cameras that is tens
    to hundreds of thousands of iterations on the Tk main thread, freezing the
    UI. Vectorised the same way viewshed_3d's raymarching already is: every
    point's geometry against every camera is one (cameras, points) numpy
    broadcast, and the line-of-sight sample batches all of one camera's still-
    uncovered candidate points into a single _sample_terrain call instead of
    one call per point. Only the gap-run bookkeeping stays a sequential walk
    (it is inherently order-dependent along the polyline); it now walks a
    precomputed boolean array instead of calling into the heavy check itself.
    """
    if len(pts) < 2 or not cams:
        return [], 0.0

    xy: List[Tuple[float, float]] = []
    for (x0, y0), (x1, y1) in zip(pts[:-1], pts[1:]):
        seg = math.hypot(x1 - x0, y1 - y0)
        if seg < 1e-6:
            continue
        n = max(1, int(seg / sample_step_m))
        for k in range(n + 1):
            t = k / n
            xy.append((x0 + t * (x1 - x0), y0 + t * (y1 - y0)))
    if not xy:
        return [], 0.0
    px = np.array([p[0] for p in xy])
    py = np.array([p[1] for p in xy])
    n_pts = px.size

    cx = np.array([c.x_m for c in cams])
    cy = np.array([c.y_m for c in cams])
    pan = np.array([c.pan_deg for c in cams])
    half_h = np.array([c.hfov_deg / 2.0 for c in cams])
    dz = np.array([c.dead_zone_m for c in cams])
    rng = np.array([_atm.usable_range_m(c.effective_range_m, visibility_km, band, weather) for c in cams])
    eye_z = np.array([c.total_z_m for c in cams])
    tilt = np.array([c.tilt_deg for c in cams])
    half_vfov = np.array([c.vfov_deg / 2.0 if c.vfov_deg > 0 else 15.0 for c in cams])
    cam_ground_z = np.array([c.ground_z_m for c in cams])

    pz = _sample_terrain(terrain, px, py) if terrain is not None else None
    # BUGFIX: this used to only check the horizontal cone + range + LOS,
    # while compute_coverage_grid also requires the point to fall inside the
    # vertical frame (tilt +/- vfov/2). A steeply tilted-down camera (tele
    # lens, wide-angle corner guard) could "cover" a fence point here that it
    # never actually frames -- the two coverage definitions disagreed on the
    # exact same placed cameras.
    ground_z_for_elev = pz[np.newaxis, :] if pz is not None else cam_ground_z[:, np.newaxis]

    dx = px[np.newaxis, :] - cx[:, np.newaxis]
    dy = py[np.newaxis, :] - cy[:, np.newaxis]
    dist = np.hypot(dx, dy)
    az = (np.degrees(np.arctan2(dx, dy)) + 360.0) % 360.0
    pan_diff = np.abs((az - pan[:, np.newaxis] + 180.0) % 360.0 - 180.0)
    in_pan = pan_diff <= half_h[:, np.newaxis]
    in_range = (dist >= dz[:, np.newaxis]) & (dist <= rng[:, np.newaxis])
    elev_ang = np.degrees(np.arctan2(ground_z_for_elev - eye_z[:, np.newaxis], np.maximum(dist, 0.1)))
    in_vfov = ((elev_ang >= (tilt - half_vfov)[:, np.newaxis])
               & (elev_ang <= (tilt + half_vfov)[:, np.newaxis]))
    candidate = in_pan & in_range & in_vfov    # (n_cams, n_pts)

    covered = np.zeros(n_pts, dtype=bool)
    if terrain is not None:
        los_ts = np.linspace(0.06, 0.97, 12)[:, np.newaxis]
        for ci in range(len(cams)):
            idx = np.where(candidate[ci] & ~covered)[0]
            if idx.size == 0:
                continue
            sx = cx[ci] + los_ts * (px[idx][np.newaxis, :] - cx[ci])
            sy = cy[ci] + los_ts * (py[idx][np.newaxis, :] - cy[ci])
            terr_h = _sample_terrain(terrain, sx.ravel(), sy.ravel()).reshape(sx.shape)
            line_h = eye_z[ci] + los_ts * (pz[idx][np.newaxis, :] - eye_z[ci])
            clear = ~np.any(terr_h > line_h + 0.5, axis=0)
            covered[idx[clear]] = True
    else:
        covered = candidate.any(axis=0)

    gap_run: List[Tuple[float, float]] = []
    gaps: List[FenceGap] = []

    def _flush_gap():
        if len(gap_run) >= 2:
            length = math.hypot(gap_run[-1][0] - gap_run[0][0], gap_run[-1][1] - gap_run[0][1])
            if length >= min_gap_m:
                gaps.append(FenceGap(gap_run[0][0], gap_run[0][1],
                                     gap_run[-1][0], gap_run[-1][1], round(length, 1)))
        gap_run.clear()

    for i in range(n_pts):
        if covered[i]:
            _flush_gap()
        else:
            gap_run.append((px[i], py[i]))
    _flush_gap()

    pct = 100.0 * float(covered.sum()) / max(n_pts, 1)
    return gaps, pct


@dataclass
class CoverageGrid:
    """Best pixels/metre reachable at each ground cell from ANY placed camera.

    FOV cone + dead zone + optical/atmospheric range, and — when a ``terrain``
    is passed — line-of-sight over the DEM (ridge occlusion). Without terrain it
    is exact on flat ground and optimistic on hills.
    """
    ppm: "np.ndarray"
    origin_x: float
    origin_y: float
    cell_m: float
    pct_by_level: Dict[str, float]      # ident / recog / observe / detect -> % of analysed area
    analysed_cells: int
    occlusion_applied: bool = False

    @property
    def covered_pct(self) -> float:
        return self.pct_by_level.get("detect", 0.0)


def _sample_terrain(terrain: TerrainData, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    """Vectorised bilinear elevation lookup. ``z_grid`` row 0 = south."""
    rows, cols = terrain.z_grid.shape
    col = np.clip((xs - terrain.origin_x) / terrain.cell_size_m, 0, cols - 1.001)
    row = np.clip((ys - terrain.origin_y) / terrain.cell_size_m, 0, rows - 1.001)
    c0 = col.astype(np.intp)
    r0 = row.astype(np.intp)
    c1 = np.minimum(c0 + 1, cols - 1)
    r1 = np.minimum(r0 + 1, rows - 1)
    fx = col - c0
    fy = row - r0
    z = terrain.z_grid
    return (z[r0, c0] * (1 - fx) * (1 - fy) + z[r0, c1] * fx * (1 - fy)
            + z[r1, c0] * (1 - fx) * fy + z[r1, c1] * fx * fy)


def compute_coverage_grid(plan: PerimeterPlanResult, camera: CameraConfig,
                          cell_m: float = 4.0, margin_m: float = 25.0,
                          visibility_km: float = 40.0, weather: str = "",
                          terrain: Optional[TerrainData] = None) -> Optional[CoverageGrid]:
    cams = plan.placed_cameras
    if not cams:
        return None

    xs = [c.x_m for c in cams]
    ys = [c.y_m for c in cams]
    reach = max((c.effective_range_m for c in cams), default=100.0)
    x0, x1 = min(xs) - margin_m, max(xs) + margin_m
    y0, y1 = min(ys) - margin_m, max(ys) + margin_m
    # bound the box to a sane size
    gx = np.arange(x0, x1 + cell_m, cell_m)
    gy = np.arange(y0, y1 + cell_m, cell_m)
    if gx.size * gy.size > 900_000 or gx.size < 2 or gy.size < 2:
        cell_m = max(cell_m, math.sqrt((x1 - x0) * (y1 - y0) / 400_000.0))
        gx = np.arange(x0, x1 + cell_m, cell_m)
        gy = np.arange(y0, y1 + cell_m, cell_m)
    mx, my = np.meshgrid(gx, gy)
    best = np.zeros_like(mx, dtype=np.float32)

    band = _atm.band_for_camera(camera.sensor_name, camera.model_name)
    optics_by_focal: Dict[float, CameraOptics] = {}

    do_occ = terrain is not None and len(cams) <= 250
    if do_occ:
        # ~1 sample per terrain cell along the ray so a one-cell-wide ridge
        # cannot slip between samples (was capped at 40 -> ~12 m spacing on a
        # long reach, thin ridges leaked through); scaled down for many cameras.
        base = int(np.clip(reach / max(terrain.cell_size_m, 1.0), 16, 90))
        n_samp = base if len(cams) <= 40 else (max(base // 2, 14) if len(cams) <= 120 else max(base // 3, 10))
        ts = np.linspace(0.03, 0.985, n_samp)[:, None, None]
        cell_z = _sample_terrain(terrain, mx, my)

    for c in cams:
        atm_reach = _atm.usable_range_m(c.effective_range_m, visibility_km, band, weather)
        # PERF: a camera's cone can never reach past atm_reach, so touching
        # the whole grid for every camera (the loop runs once per placed
        # camera) wasted almost all of its work on cells no camera could
        # ever mark covered -- this was the actual multi-second freeze on a
        # long perimeter with many short-range cameras, not the fence-line
        # analysis above. Slice a local window (camera position +/- reach,
        # one cell of margin) and only touch that; results are identical
        # since every cell outside it fails the dist <= atm_reach test
        # anyway, this only skips computing that fact the expensive way.
        margin = atm_reach + cell_m
        col_lo = int(np.clip(math.floor((c.x_m - margin - gx[0]) / cell_m), 0, gx.size - 1))
        col_hi = int(np.clip(math.ceil((c.x_m + margin - gx[0]) / cell_m), 0, gx.size - 1)) + 1
        row_lo = int(np.clip(math.floor((c.y_m - margin - gy[0]) / cell_m), 0, gy.size - 1))
        row_hi = int(np.clip(math.ceil((c.y_m + margin - gy[0]) / cell_m), 0, gy.size - 1)) + 1
        if col_hi <= col_lo or row_hi <= row_lo:
            continue
        mx_w = mx[row_lo:row_hi, col_lo:col_hi]
        my_w = my[row_lo:row_hi, col_lo:col_hi]

        dx = mx_w - c.x_m
        dy = my_w - c.y_m
        dist = np.hypot(dx, dy)
        az = np.degrees(np.arctan2(dx, dy))
        off = np.abs((az - c.pan_deg + 180.0) % 360.0 - 180.0)
        in_cone = (off <= c.hfov_deg / 2.0) & (dist >= c.dead_zone_m) & (dist <= atm_reach)

        # Vertical framing: the ground cell must sit between the bottom and top
        # rays (tilt +/- vfov/2). Without this the heatmap credits far cells the
        # tilted-down camera never frames.
        eye_z = c.ground_z_m + c.mast_height_m
        gz = cell_z[row_lo:row_hi, col_lo:col_hi] if do_occ else float(c.ground_z_m)
        elev_ang = np.degrees(np.arctan2(gz - eye_z, np.maximum(dist, 0.1)))
        half_vfov = c.vfov_deg / 2.0 if c.vfov_deg > 0 else 15.0
        in_cone &= (elev_ang >= c.tilt_deg - half_vfov) & (elev_ang <= c.tilt_deg + half_vfov)

        if do_occ:
            sx = c.x_m + ts * dx
            sy = c.y_m + ts * dy
            terr = _sample_terrain(terrain, sx, sy)
            line = eye_z + ts * (gz - eye_z)
            visible = ~np.any(terr > line + 0.5, axis=0)
            in_cone &= visible

        slant = np.hypot(dist, c.mast_height_m)
        optics = optics_by_focal.get(c.focal_mm)
        if optics is None:
            optics = optics_by_focal[c.focal_mm] = camera_optics(camera, c.focal_mm)
        ppm = optics.ppm_at(np.maximum(slant, 1.0))
        best_w = best[row_lo:row_hi, col_lo:col_hi]
        best[row_lo:row_hi, col_lo:col_hi] = np.where(in_cone, np.maximum(best_w, ppm), best_w)

    # Denominator = the band we are meant to protect (within band_m of the
    # fence), so degraded weather reads as WORSE protected-zone coverage, not a
    # smaller pie. Falls back to the covered cells when no fence is available.
    band_m = 40.0
    protect = _fence_band_mask(mx, my, plan.fence_points, band_m)
    if protect is None or not protect.any():
        protect = best > 0.0
    analysed = int(np.count_nonzero(protect))
    denom = float(max(analysed, 1))
    best_in = np.where(protect, best, 0.0)
    pct = {
        "ident": 100.0 * np.count_nonzero(best_in >= PPM_IDENT) / denom,
        "recog": 100.0 * np.count_nonzero(best_in >= PPM_RECOG) / denom,
        "observe": 100.0 * np.count_nonzero(best_in >= PPM_OBSERVE) / denom,
        "detect": 100.0 * np.count_nonzero(best_in >= PPM_DETECT) / denom,
    }
    return CoverageGrid(ppm=best, origin_x=float(gx[0]), origin_y=float(gy[0]),
                        cell_m=float(cell_m), pct_by_level=pct, analysed_cells=analysed,
                        occlusion_applied=bool(do_occ))


def _fence_band_mask(mx, my, fence_points, band_m):
    if not fence_points or len(fence_points) < 2:
        return None
    d2 = np.full(mx.shape, np.inf, dtype=np.float32)
    pts = fence_points
    for (x0, y0), (x1, y1) in zip(pts[:-1], pts[1:]):
        vx, vy = x1 - x0, y1 - y0
        seg2 = vx * vx + vy * vy
        if seg2 < 1e-6:
            continue
        t = np.clip(((mx - x0) * vx + (my - y0) * vy) / seg2, 0.0, 1.0)
        px, py = x0 + t * vx, y0 + t * vy
        d2 = np.minimum(d2, (mx - px) ** 2 + (my - py) ** 2)
    return d2 <= band_m * band_m
