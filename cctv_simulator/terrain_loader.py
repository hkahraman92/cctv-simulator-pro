"""
Digital Elevation Model (DEM) and Terrain Loader for 3D CCTV Camera Placement.

Supports:
1. Procedural topographical terrain generation (Ridge & Valley, Mountain Outpost, Canyon, Rolling Hills)
2. Local GeoTIFF / DEM / Heightmap file loading with CRS degree-to-meter conversion
3. User-calibrated physical width scaling and elevation profiling
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np


@dataclass
class TerrainData:
    """Holds 2D elevation raster matrix and spatial georeferencing."""
    z_grid: np.ndarray             # 2D array of elevations in meters (shape: [rows, cols])
    cell_size_m: float = 5.0       # Spatial resolution (meters per pixel/cell)
    origin_x: float = 0.0          # Local or UTM X coordinate of bottom-left
    origin_y: float = 0.0          # Local or UTM Y coordinate of bottom-left
    name: str = "Özel Arazi"
    lat_center: Optional[float] = None
    lon_center: Optional[float] = None
    # True only when z_grid came from a real DEM (GeoTIFF, Terrarium tiles,
    # elevation API). Procedural presets and fallbacks leave it False so the UI
    # and the exported reports can never present invented relief as survey data.
    is_measured: bool = False
    source_note: str = ""

    @property
    def is_synthetic(self) -> bool:
        return not self.is_measured

    @property
    def rows(self) -> int:
        return self.z_grid.shape[0]

    @property
    def cols(self) -> int:
        return self.z_grid.shape[1]

    @property
    def width_m(self) -> float:
        return self.cols * self.cell_size_m

    @property
    def height_m(self) -> float:
        return self.rows * self.cell_size_m

    @property
    def min_elev_m(self) -> float:
        return float(np.nanmin(self.z_grid))

    @property
    def max_elev_m(self) -> float:
        return float(np.nanmax(self.z_grid))

    def set_custom_width_m(self, target_width_m: float):
        """Calibrates spatial resolution by setting exact physical width in meters."""
        if target_width_m > 10.0 and self.cols > 0:
            self.cell_size_m = target_width_m / float(self.cols)

    def get_elevation_at(self, x_m: float, y_m: float) -> float:
        """Returns bilinear interpolated elevation at local meter coordinates (x_m, y_m)."""
        col = (x_m - self.origin_x) / self.cell_size_m
        row = (y_m - self.origin_y) / self.cell_size_m

        if col < 0 or col >= self.cols - 1 or row < 0 or row >= self.rows - 1:
            c_idx = max(0, min(self.cols - 1, int(round(col))))
            r_idx = max(0, min(self.rows - 1, int(round(row))))
            return float(self.z_grid[r_idx, c_idx])

        c0, r0 = int(col), int(row)
        c1, r1 = c0 + 1, r0 + 1
        fx = col - c0
        fy = row - r0

        z00 = self.z_grid[r0, c0]
        z01 = self.z_grid[r0, c1]
        z10 = self.z_grid[r1, c0]
        z11 = self.z_grid[r1, c1]

        z_top = z00 * (1.0 - fx) + z01 * fx
        z_bot = z10 * (1.0 - fx) + z11 * fx
        return float(z_top * (1.0 - fy) + z_bot * fy)

    def get_profile_between(self, x0: float, y0: float, x1: float, y1: float,
                            num_samples: int = 200) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Extracts distance and elevation profile between two points."""
        dist_total = math.hypot(x1 - x0, y1 - y0)
        ts = np.linspace(0.0, 1.0, num_samples)
        xs = x0 + ts * (x1 - x0)
        ys = y0 + ts * (y1 - y0)
        dists = ts * dist_total

        elevs = np.array([self.get_elevation_at(x, y) for x, y in zip(xs, ys)])
        coords = np.column_stack((xs, ys))
        return dists, elevs, coords


def generate_procedural_terrain(preset: str = "ridge_and_valley",
                                 grid_size: int = 200,
                                 cell_size_m: float = 10.0) -> TerrainData:
    """Generates realistic topographical terrain grids (default: 2000m x 2000m for 200x200 grid)."""
    x = np.linspace(-1.0, 1.0, grid_size)
    y = np.linspace(-1.0, 1.0, grid_size)
    xx, yy = np.meshgrid(x, y)

    if preset == "ridge_and_valley" or preset == "Hâkim Tepe & Vadi":
        hill1 = 45.0 * np.exp(-((xx + 0.4)**2 + (yy + 0.3)**2) / 0.12)
        hill2 = 30.0 * np.exp(-((xx - 0.5)**2 + (yy - 0.2)**2) / 0.18)
        ridge = 22.0 * np.cos(3.0 * xx + yy) * np.exp(-(yy**2) / 0.4)
        noise = 3.5 * np.sin(10.0 * xx) * np.cos(10.0 * yy)
        z = hill1 + hill2 + ridge + noise + 100.0
        name = "Hâkim Tepe & Vadi (Ridge & Valley)"

    elif preset == "border_outpost" or preset == "Sınır Karakolu & Dağlık Arazi":
        outpost_peak = 75.0 * np.exp(-((xx + 0.5)**2 + (yy + 0.5)**2) / 0.10)
        front_ridge = 40.0 * np.exp(-((xx - 0.1)**2 + (yy + 0.1)**2) / 0.08)
        mountain_range = 60.0 * (0.5 * np.sin(2.5 * xx) + 0.5 * np.cos(2.0 * yy))
        noise = 4.0 * np.sin(12.0 * xx + 4.0 * yy)
        z = outpost_peak + front_ridge + mountain_range + noise + 250.0
        name = "Sınır Karakolu & Dağlık Arazi"

    elif preset == "canyon" or preset == "Kanyon & Geçit":
        canyon_depth = -45.0 * np.exp(-(xx**2) / 0.08)
        plateau_left = 35.0 / (1.0 + np.exp(-10.0 * (xx + 0.3)))
        plateau_right = 35.0 / (1.0 + np.exp(10.0 * (xx - 0.3)))
        undulation = 12.0 * np.sin(4.0 * yy)
        z = canyon_depth + plateau_left + plateau_right + undulation + 150.0
        name = "Kanyon & Geçit (Canyon Gorge)"

    else:
        z = (18.0 * np.sin(3.0 * xx) * np.cos(3.0 * yy)
             + 10.0 * np.sin(6.0 * xx + 2.0 * yy)
             + 4.0 * np.cos(12.0 * xx) + 50.0)
        name = "Düzlük & Tümsekler (Rolling Hills)"

    return TerrainData(z_grid=z, cell_size_m=cell_size_m, name=name,
                       is_measured=False,
                       source_note="Prosedürel örnek arazi - ölçüm değildir.")


def load_geotiff_or_dem(filepath: str | Path) -> TerrainData:
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"Arazi dosyası bulunamadı: {filepath}")

    try:
        import rasterio
        with rasterio.open(path) as src:
            z_grid = src.read(1).astype(np.float32)
            if src.nodata is not None:
                z_grid[z_grid == src.nodata] = np.nan
            transform = src.transform
            raw_cell_size = float(abs(transform[0]))
            origin_x = float(transform[2])
            origin_y = float(transform[5])

            # Convert Geographic Coordinates (degrees EPSG:4326) to meters
            if raw_cell_size < 0.05:
                # 1 arc-second ~ 30.87m, 1 degree ~ 111,320m
                cell_size_m = raw_cell_size * 111320.0
            else:
                cell_size_m = raw_cell_size

            return TerrainData(
                z_grid=z_grid,
                cell_size_m=max(cell_size_m, 1.0),
                origin_x=origin_x,
                origin_y=origin_y,
                name=path.stem,
                is_measured=True,
                source_note=f"GeoTIFF/DEM: {path.name}",
            )
    except Exception:
        pass

    try:
        from PIL import Image
        img = Image.open(path)
        arr = np.array(img, dtype=np.float32)
        if arr.ndim == 3:
            arr = 0.299 * arr[:, :, 0] + 0.587 * arr[:, :, 1] + 0.114 * arr[:, :, 2]
        # Default ~2000m total width
        default_cell_m = 2000.0 / float(max(arr.shape[1], 1))
        # A plain greyscale heightmap carries no vertical datum, so the values
        # are relative, not metres above sea level. Not a measurement.
        return TerrainData(z_grid=arr, cell_size_m=default_cell_m, name=path.stem,
                           is_measured=False,
                           source_note=f"Gri tonlamalı yükseklik haritası ({path.name}) - "
                                       f"değerler göreli, metre değil.")
    except Exception as exc:
        raise ValueError(f"Arazi dosyası okunamadı ({path.suffix}): {exc}")
