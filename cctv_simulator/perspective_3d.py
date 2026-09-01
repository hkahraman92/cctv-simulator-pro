"""
3D Perspective Projection & Camera Eye View Engine for CCTV Simulation.
Provides JVSG-style 3D camera sensor view, ground perspective grid,
DORI visual zones, 3D human mannequin, vehicle & license plate targets,
and live PPM image degradation/pixelation simulation.
"""
import math
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass

from .config import SENSOR_DIMS_MM, RESOLUTIONS
from .models import CameraConfig, PPMLevel, OpticResult


# PERF: __slots__ removes the per-instance __dict__; a 3D frame allocates
# hundreds-to-thousands of these. (Requires Python >= 3.10.)
@dataclass(slots=True)
class Point3D:
    x: float  # Lateral offset (m), positive = right, negative = left
    y: float  # Forward distance along ground (m)
    z: float  # Height above ground (m), 0 = ground level


@dataclass(slots=True)
class ProjectedPoint:
    u: float  # Viewport X (pixels)
    v: float  # Viewport Y (pixels)
    depth: float  # Distance from camera lens (m)
    visible: bool  # True if in front of camera


class Perspective3DEngine:
    """Calculates 3D transformations from world coordinates to camera viewport."""

    def __init__(self, camera: CameraConfig, focal_mm: Optional[float] = None, viewport_size: Tuple[int, int] = (800, 480)):
        self.camera = camera
        self.focal_mm = focal_mm or camera.focal_min_mm
        self.viewport_w, self.viewport_h = viewport_size

        self.sensor_w_mm, self.sensor_h_mm = SENSOR_DIMS_MM.get(camera.sensor_name, (5.37, 3.02))
        res_info = RESOLUTIONS.get(camera.resolution_name, (2688, 1520))
        self.res_w, self.res_h = res_info

        self.pole_h_m = max(camera.pole_height_m, 0.1)
        self.tilt_deg = camera.tilt_deg
        self.tilt_rad = math.radians(self.tilt_deg)
        self.cos_tilt = math.cos(self.tilt_rad)
        self.sin_tilt = math.sin(self.tilt_rad)

        # Field of view
        self.hfov_rad = 2 * math.atan(self.sensor_w_mm / (2 * self.focal_mm))
        self.vfov_rad = 2 * math.atan(self.sensor_h_mm / (2 * self.focal_mm))
        self.hfov_deg = math.degrees(self.hfov_rad)
        self.vfov_deg = math.degrees(self.vfov_rad)

        # PERF: these four constants were recomputed inside project_point on
        # EVERY projected point (4 divisions per call). Hoisted to __init__.
        self._kx = self.focal_mm / (self.sensor_w_mm / 2.0)
        self._ky = self.focal_mm / (self.sensor_h_mm / 2.0)
        self._half_w = self.viewport_w / 2.0
        self._half_h = self.viewport_h / 2.0

        # PERF: is_thermal used to be a property doing 3 str.upper() plus 5
        # substring scans per read, and it is read inside the grid/DORI loops.
        sens = camera.sensor_name.upper()
        res_name = camera.resolution_name.upper()
        mod = camera.model_name.upper()
        self._is_thermal = ("LWIR" in sens or "MWIR" in sens or "LWIR" in res_name
                            or "TERMAL" in mod or "THERMAL" in mod)

    def set_viewport_size(self, w: int, h: int):
        self.viewport_w = max(w, 200)
        self.viewport_h = max(h, 150)
        self._half_w = self.viewport_w / 2.0   # PERF: keep cached halves in sync
        self._half_h = self.viewport_h / 2.0

    def project_point(self, pt: Point3D) -> ProjectedPoint:
        """Projects a 3D point in front of camera into 2D viewport coordinates.

        PERF: bit-identical to the original, but uses the constants cached in
        __init__ and a single reciprocal instead of two divisions.
        """
        dy = pt.y
        if dy < 0.001:
            dy = 0.001
        dz = pt.z - self.pole_h_m

        # Camera coordinate transformation (pitch/tilt rotation around X axis)
        yc = dy * self.cos_tilt - dz * self.sin_tilt
        if yc <= 0.05:  # Behind camera or on lens plane
            return ProjectedPoint(0.0, 0.0, yc, False)
        zc = dy * self.sin_tilt + dz * self.cos_tilt

        # Normalized coordinates [-1, 1] relative to sensor FOV
        inv_yc = 1.0 / yc
        norm_x = pt.x * inv_yc * self._kx
        norm_y = zc * inv_yc * self._ky

        half_w = self._half_w
        half_h = self._half_h
        return ProjectedPoint(
            half_w + norm_x * half_w,
            half_h - norm_y * half_h,
            yc,
            -2.0 <= norm_x <= 2.0 and -2.0 <= norm_y <= 2.0,
        )

    def project_many(self, points) -> List[ProjectedPoint]:
        """Batch projection.

        PERF: localises every engine attribute once instead of ~10 attribute
        loads per point. Use for grid / DORI band / mesh generation.
        """
        cos_t, sin_t = self.cos_tilt, self.sin_tilt
        pole, kx, ky = self.pole_h_m, self._kx, self._ky
        half_w, half_h = self._half_w, self._half_h
        out: List[ProjectedPoint] = []
        add = out.append
        for pt in points:
            dy = pt.y if pt.y > 0.001 else 0.001
            dz = pt.z - pole
            yc = dy * cos_t - dz * sin_t
            if yc <= 0.05:
                add(ProjectedPoint(0.0, 0.0, yc, False))
                continue
            inv = 1.0 / yc
            nx = pt.x * inv * kx
            ny = (dy * sin_t + dz * cos_t) * inv * ky
            add(ProjectedPoint(half_w + nx * half_w, half_h - ny * half_h, yc,
                               -2.0 <= nx <= 2.0 and -2.0 <= ny <= 2.0))
        return out

    def get_horizon_y(self) -> float:
        """Calculates the screen Y coordinate of the horizon (vanishing line)."""
        # Horizon is vanishing line of ground plane as distance -> infinity
        # At infinity, dz/dy -> 0, so zc/yc -> sin(tilt)/cos(tilt) = tan(tilt)
        norm_y_horizon = math.tan(self.tilt_rad) * self._ky
        return self._half_h - norm_y_horizon * self._half_h

    @property
    def is_thermal(self) -> bool:
        """True if the active camera is a thermal LWIR/MWIR sensor.

        PERF: precomputed in __init__ (was 3 str.upper() + 5 scans per read).
        """
        return self._is_thermal

    def calculate_ppm_at_distance(self, distance_m: float, target_h_m: float = 1.8) -> float:
        """Calculates actual horizontal pixels per meter at a given distance."""
        v_drop = max(self.pole_h_m - target_h_m, 0.0)
        opt_dist = math.sqrt(distance_m * distance_m + v_drop * v_drop)
        if opt_dist < 0.01:
            return 9999.0
        return (self.res_w * self.focal_mm) / (opt_dist * self.sensor_w_mm)

    def get_dori_status(self, ppm: float, target_h_m: float = 1.8) -> Tuple[str, str, str]:
        """Returns (DORI/DRI Name, Badge Color, Description). Adapts for Thermal & Optical."""
        if self._is_thermal:
            target_px = ppm * target_h_m
            pct_short_edge = (target_px / max(self.res_h, 1)) * 100.0
            if target_px >= 24.0:
                return ("Johnson: Teşhis (Id - 12 cyc)", "#FF5722", f"Hedefte {target_px:.1f} px | Johnson Teşhis Eşiği (12 çizgi çifti) sağlandı")
            elif target_px >= 12.0:
                return ("Johnson: Tanıma (Rec - 6 cyc)", "#0288D1", f"Hedefte {target_px:.1f} px | Johnson Tanıma Eşiği (6 çizgi çifti) sağlandı")
            elif pct_short_edge >= 1.5 or target_px >= 7.68:
                return ("Algoritma: %1.5 Tespit OK", "#8E24AA", f"Hedefte {target_px:.1f} px (%{pct_short_edge:.2f} kısa kenar) | Algoritma VCA tespit eşiği sağlandı")
            elif target_px >= 3.0:
                return ("Johnson: Algılama (Det - 1.5 cyc)", "#4CAF50", f"Hedefte {target_px:.1f} px | Johnson Algılama Eşiği (1.5 çizgi çifti) sağlandı")
            else:
                return ("Kapsama Dışı (Piksel Yetersiz)", "#78909C", f"Hedefte {target_px:.1f} px (%{pct_short_edge:.2f}) | Algoritma ve Johnson eşiği altında")

        # Standard Optical DORI
        if ppm >= 250.0:
            return ("Kimlik Tespiti (Identification)", "#FF0039", "Yüz hatları ve detaylar kesin olarak teşhis edilir (250+ PPM)")
        elif ppm >= 125.0:
            return ("Tanıma / Teşhis (Recognition)", "#FF7518", "Şahsın kim olduğu biliniyorsa net tanınabilir (125-250 PPM)")
        elif ppm >= 62.5:
            return ("Gözlem (Observation)", "#2780E3", "Kıyafet, cinsiyet ve genel hareketler izlenebilir (62.5-125 PPM)")
        elif ppm >= 25.0:
            return ("Algılama (Detection)", "#3FB618", "İnsan/araç varlığı ayırt edilebilir, yüz belirsizdir (25-62.5 PPM)")
        else:
            return ("İzleme / Yetersiz (Monitoring)", "#6C757D", "Sadece hareketli silüet görünür, detay yok (<25 PPM)")


def generate_ground_grid_lines(engine: Perspective3DEngine, max_dist_m: float = 60.0) -> List[Dict[str, Any]]:
    """Generates 3D perspective ground grid lines with distance markers."""
    lines = []
    if engine._is_thermal or max_dist_m > 200.0:
        distances = [10.0, 50.0, 100.0, 250.0, 500.0, 1000.0, 2000.0, 3000.0, 5000.0, 8000.0]
        lateral_span = max(100.0, max_dist_m * 0.35)
    else:
        distances = [2.0, 5.0, 10.0, 15.0, 20.0, 30.0, 40.0, 50.0, 60.0, 80.0, 100.0]
        lateral_span = 25.0  # meters left/right

    # 1. Transverse distance lines (perpendicular to optical axis)
    for dist in distances:
        if dist > max_dist_m * 1.6:
            continue
        p_left = engine.project_point(Point3D(-lateral_span, dist, 0.0))
        p_right = engine.project_point(Point3D(lateral_span, dist, 0.0))
        p_mid = engine.project_point(Point3D(0.0, dist, 0.0))

        lines.append({
            "type": "transverse",
            "p1": p_left,
            "p2": p_right,
            "p_mid": p_mid,
            "distance": dist,
            "is_major": dist in [5.0, 10.0, 20.0, 50.0, 100.0, 500.0, 1000.0, 2000.0, 5000.0],
        })

    # 2. Longitudinal lines (parallel to optical axis)
    if engine._is_thermal or max_dist_m > 200.0:
        long_offsets = [-lateral_span * 0.6, -lateral_span * 0.3, 0.0, lateral_span * 0.3, lateral_span * 0.6]
    else:
        long_offsets = [-15.0, -10.0, -5.0, -2.0, 0.0, 2.0, 5.0, 10.0, 15.0]

    for x_off in long_offsets:
        p_near = engine.project_point(Point3D(x_off, 1.5, 0.0))
        p_far = engine.project_point(Point3D(x_off, min(max_dist_m * 1.3, 10000.0), 0.0))
        lines.append({
            "type": "longitudinal",
            "p1": p_near,
            "p2": p_far,
            "is_center": abs(x_off) < 0.01,
        })

    return lines


def generate_dori_ground_polygons(engine: Perspective3DEngine, ppm_levels: List[PPMLevel]) -> List[Dict[str, Any]]:
    """Generates colored 3D ground zones corresponding to EN 62676-4 DORI PPM thresholds."""
    polygons = []
    lateral_span = 20.0

    # Calculate ground distances for each standard PPM level
    sorted_levels = sorted(ppm_levels, key=lambda lvl: lvl.ppm, reverse=True)
    prev_dist = 0.5

    for lvl in sorted_levels:
        if lvl.ppm <= 0:
            continue
        # Distance where PPM reaches this threshold
        # PPM = (ResW * f) / (opt_dist * SensorW) => opt_dist = (ResW * f) / (PPM * SensorW)
        opt_dist = (engine.res_w * engine.focal_mm) / (lvl.ppm * engine.sensor_w_mm)
        v_drop = max(engine.pole_h_m - 1.8, 0.0)
        if opt_dist > v_drop:
            ground_dist = math.sqrt(opt_dist**2 - v_drop**2)
        else:
            ground_dist = 0.5

        if ground_dist > prev_dist:
            d_start = prev_dist
            d_end = min(ground_dist, 100.0)

            # 4 corners of ground band in 3D
            p1 = engine.project_point(Point3D(-lateral_span, d_start, 0.0))
            p2 = engine.project_point(Point3D(lateral_span, d_start, 0.0))
            p3 = engine.project_point(Point3D(lateral_span, d_end, 0.0))
            p4 = engine.project_point(Point3D(-lateral_span, d_end, 0.0))

            polygons.append({
                "level": lvl,
                "d_start": d_start,
                "d_end": d_end,
                "points": [p1, p2, p3, p4],
                "color": lvl.color,
            })
            prev_dist = d_end

    return polygons
