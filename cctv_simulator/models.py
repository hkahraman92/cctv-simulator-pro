from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class PPMLevel:
    key: str
    name: str
    ppm: float
    color: str
    level_type: str


@dataclass
class CameraConfig:
    name: str = "Kamera 1"
    model_name: str = "Özel kamera"
    sensor_name: str = '1/2.8"'
    resolution_name: str = "4 MP (2K - 2688x1520)"
    focal_min_mm: float = 2.8
    focal_max_mm: float = 12.0
    pole_height_m: float = 4.0
    tilt_deg: float = 15.0
    target_height_m: float = 1.8
    pos_x_m: float = 0.0
    pos_y_m: float = 0.0
    heading_deg: float = 0.0
    ir_range_m: float = 30.0
    min_lux: float = 0.01


@dataclass
class TargetPoint:
    active: bool = False
    name: str = "Kontrol Noktası"
    x_m: float = 12.0
    y_m: float = 0.0
    required_level_key: str = "ANPR_Alg"


@dataclass
class AnalysisRow:
    camera: str
    mode: str
    level: str
    level_type: str
    ppm: float
    optical_distance_m: float
    ground_distance_m: float
    status: str


@dataclass
class OpticResult:
    camera: CameraConfig
    mode: str
    focal_mm: float
    hfov_deg: float
    vfov_deg: float
    bottom_ray_deg: float
    top_ray_deg: float
    dead_zone_m: float
    max_geom_dist_m: float
    vertical_drop_m: float
    sensor_width_mm: float
    res_width_px: int
    ground_distances: Dict[str, float]
    rows: List[AnalysisRow]
    recommendations: List[str] = field(default_factory=list)
    dead_zone_area_m2: float = 0.0
    dead_zone_near_m: float = 0.0
    dead_zone_left_m: float = 0.0
    dead_zone_right_m: float = 0.0
    dead_zone_covered_by: Optional[List[str]] = None


DEFAULT_LEVELS = [
    # ── Termal: Johnson Kriterleri (NATO STANAG 4347 / TR 01/2007) ──
    PPMLevel("Johnson_Human_Det", "Johnson: İnsan Algılama (1.5 cyc)", 1.67, "#3FB618", "Termal"),
    PPMLevel("Algo_Human_Det", "Algoritma: İnsan Tespiti (%1.5 Kısa Kenar)", 4.27, "#8E24AA", "Algoritma"),
    PPMLevel("Johnson_Human_Rec", "Johnson: İnsan Tanıma (6.0 cyc)", 6.67, "#2780E3", "Termal"),
    PPMLevel("Johnson_Human_Id", "Johnson: İnsan Teşhis (12.0 cyc)", 13.33, "#FF7518", "Termal"),
    PPMLevel("Johnson_Veh_Det", "Johnson: Araç Algılama (1.5 cyc)", 1.30, "#4CAF50", "Termal"),
    PPMLevel("Algo_Veh_Det", "Algoritma: Araç Tespiti (%1.5 Kısa Kenar)", 3.34, "#6A1B9A", "Algoritma"),
    PPMLevel("Johnson_Veh_Rec", "Johnson: Araç Tanıma (6.0 cyc)", 5.22, "#0288D1", "Termal"),
    PPMLevel("Johnson_Veh_Id", "Johnson: Araç Teşhis (12.0 cyc)", 10.43, "#FF5722", "Termal"),

    # ── Optik: EN 62676-4 ve Özel Güvenlik Seviyeleri ──
    PPMLevel("Scrutinize", "Optik: Scrutinize", 1500, "#FF8A80", "Standart"),
    PPMLevel("Face_Detect", "Optik: Yüz Tespit", 794, "#FFB74D", "Algoritma"),
    PPMLevel("Validate", "Optik: Validate", 500, "#FFF59D", "Standart"),
    PPMLevel("Characterize", "Optik: Kimlik Tespiti (250 PPM)", 250, "#D4E157", "Standart"),
    PPMLevel("ANPR_Alg", "Optik: TR Plaka (143 PPM)", 143, "#CE93D8", "Algoritma"),
    PPMLevel("Perceive", "Optik: Tanıma / Teşhis (125 PPM)", 125, "#A5D6A7", "Standart"),
    PPMLevel("Discern", "Optik: Gözlem (80 PPM)", 80, "#81C784", "Standart"),
    PPMLevel("Outline", "Optik: Algılama (40 PPM)", 40, "#80DEEA", "Standart"),
    PPMLevel("Overview", "Optik: İzleme (20 PPM)", 20, "#90CAF9", "Standart"),
]
