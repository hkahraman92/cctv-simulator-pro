"""Saved map regions: a downloaded (or loaded) terrain + orthophoto kept on
disk so the area reopens offline, instantly, without re-entering coordinates
or re-downloading.

The tile cache (online_map_loader) only keeps individual tiles: a re-download
still rebuilds the mosaic, and the elevation-API fallback is not cached at
all. A region is the finished product -- the exact TerrainData the analyses
ran on (z_grid bit-for-bit, georeference, ``is_measured``) plus the image.

Layout, one directory per region under ``user_data_dir()/regions``:

    <region_id>/region.json   metadata (name, centre, extent, source, ...)
    <region_id>/dem.npz       z_grid + cell size + origin (compressed)
    <region_id>/ortho.jpg|png orthophoto (JPEG for photo sources, PNG for maps)

Writes go to a temporary sibling directory that is renamed into place, so a
crash never leaves a half-written region that lists as valid. No Tk.
"""
from __future__ import annotations

import json
import re
import shutil
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
from PIL import Image

from .terrain_loader import TerrainData

FORMAT_VERSION = 1
_META = "region.json"
_DEM = "dem.npz"
# Photographic imagery compresses ~10x better as JPEG with no visible loss;
# street / topo maps carry text and hard edges and stay lossless.
_PHOTO_SOURCES = {"esri"}
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,80}$")


@dataclass
class RegionInfo:
    region_id: str
    name: str
    created: str                       # ISO local time
    lat_center: Optional[float] = None
    lon_center: Optional[float] = None
    width_m: float = 0.0
    height_m: float = 0.0
    cell_size_m: float = 0.0
    source: str = ""                   # esri / osm / opentopo / local / ...
    quality: str = ""
    zoom: Optional[int] = None
    m_per_px: Optional[float] = None
    attribution: str = ""
    terrain_name: str = ""
    source_note: str = ""
    is_measured: bool = False
    image_file: str = ""               # "" = no orthophoto saved
    image_size: Tuple[int, int] = (0, 0)
    size_bytes: int = 0
    extra: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        loc = (f"{self.lat_center:.4f}, {self.lon_center:.4f}"
               if self.lat_center is not None and self.lon_center is not None else "konum yok")
        return f"{self.name} — {loc} · {self.width_m / 1000:.1f} km"


def regions_dir(base: Optional[Path] = None) -> Path:
    if base is not None:
        return Path(base)
    from .config import user_data_dir
    return Path(user_data_dir()) / "regions"


def _slug(name: str) -> str:
    # Turkish letters to ASCII for the directory name only (Kural 4: the
    # display name keeps the real characters).
    s = name.translate(str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU"))
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return (s or "bolge")[:48]


def _check_id(region_id: str) -> str:
    # The id becomes a path component: never let "..", separators or drive
    # letters through.
    if not isinstance(region_id, str) or not _ID_RE.match(region_id):
        raise ValueError(f"Geçersiz bölge kimliği: {region_id!r}")
    return region_id


def _dir_size(path: Path) -> int:
    return sum(p.stat().st_size for p in path.iterdir() if p.is_file())


def save_region(name: str, terrain: TerrainData, image: Optional[Image.Image] = None, *,
                source: str = "", quality: str = "", zoom: Optional[int] = None,
                m_per_px: Optional[float] = None, attribution: str = "",
                extra: Optional[dict] = None, base: Optional[Path] = None) -> RegionInfo:
    """Write ``terrain`` (+ optional orthophoto) as a new region; returns its info."""
    name = (name or "").strip() or "Adsız bölge"
    root = regions_dir(base)
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    region_id = f"{_slug(name)}-{stamp}"
    n = 2
    while (root / region_id).exists():
        region_id = f"{_slug(name)}-{stamp}-{n}"
        n += 1
    _check_id(region_id)

    tmp = root / f".tmp-{region_id}"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir()
    try:
        np.savez_compressed(tmp / _DEM, z_grid=terrain.z_grid,
                            cell_size_m=np.float64(terrain.cell_size_m),
                            origin_x=np.float64(terrain.origin_x),
                            origin_y=np.float64(terrain.origin_y))
        image_file, image_size = "", (0, 0)
        if image is not None:
            if source in _PHOTO_SOURCES:
                image_file = "ortho.jpg"
                image.convert("RGB").save(tmp / image_file, "JPEG", quality=92, optimize=True)
            else:
                image_file = "ortho.png"
                image.save(tmp / image_file, "PNG", optimize=False)
            image_size = (int(image.width), int(image.height))
        info = RegionInfo(
            region_id=region_id, name=name, created=datetime.now().isoformat(timespec="seconds"),
            lat_center=terrain.lat_center, lon_center=terrain.lon_center,
            width_m=float(terrain.width_m), height_m=float(terrain.height_m),
            cell_size_m=float(terrain.cell_size_m), source=source, quality=quality,
            zoom=zoom, m_per_px=m_per_px, attribution=attribution,
            terrain_name=terrain.name, source_note=terrain.source_note,
            is_measured=bool(terrain.is_measured), image_file=image_file,
            image_size=image_size, extra=dict(extra or {}),
        )
        meta = asdict(info)
        meta.pop("size_bytes")
        meta["format_version"] = FORMAT_VERSION
        (tmp / _META).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.rename(root / region_id)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    info.size_bytes = _dir_size(root / region_id)
    return info


def _read_info(path: Path) -> RegionInfo:
    meta = json.loads((path / _META).read_text(encoding="utf-8"))
    meta.pop("format_version", None)
    known = {k: v for k, v in meta.items() if k in RegionInfo.__dataclass_fields__}
    if "image_size" in known:
        known["image_size"] = tuple(known["image_size"])
    info = RegionInfo(**known)
    if info.region_id != path.name:
        raise ValueError("region_id / dizin adı uyuşmuyor")
    info.size_bytes = _dir_size(path)
    return info


def list_regions(base: Optional[Path] = None) -> List[RegionInfo]:
    """Saved regions, newest first. Broken or half-written entries are skipped."""
    root = regions_dir(base)
    if not root.is_dir():
        return []
    out: List[RegionInfo] = []
    for d in root.iterdir():
        if not d.is_dir() or d.name.startswith(".") or not _ID_RE.match(d.name):
            continue
        try:
            info = _read_info(d)
            if not (d / _DEM).is_file():
                continue
            out.append(info)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
    out.sort(key=lambda r: r.created, reverse=True)
    return out


def load_region(region_id: str, base: Optional[Path] = None
                ) -> Tuple[TerrainData, Optional[Image.Image], RegionInfo]:
    """The saved TerrainData (bit-identical z_grid) and orthophoto."""
    path = regions_dir(base) / _check_id(region_id)
    if not path.is_dir():
        raise FileNotFoundError(f"Kayıtlı bölge bulunamadı: {region_id}")
    info = _read_info(path)
    with np.load(path / _DEM, allow_pickle=False) as dem:
        terrain = TerrainData(
            z_grid=dem["z_grid"], cell_size_m=float(dem["cell_size_m"]),
            origin_x=float(dem["origin_x"]), origin_y=float(dem["origin_y"]),
            name=info.terrain_name or info.name, lat_center=info.lat_center,
            lon_center=info.lon_center, is_measured=info.is_measured,
            source_note=info.source_note,
        )
    image = None
    if info.image_file:
        with Image.open(path / info.image_file) as im:
            image = im.convert("RGBA")
    return terrain, image, info


def delete_region(region_id: str, base: Optional[Path] = None) -> None:
    path = regions_dir(base) / _check_id(region_id)
    if path.is_dir():
        shutil.rmtree(path)


def find_region(lat: float, lon: float, width_m: float, source: str,
                base: Optional[Path] = None, tol_m: float = 25.0) -> Optional[RegionInfo]:
    """A saved region covering the same request (centre within ``tol_m``,
    same width and source), so a repeat download can be skipped."""
    import math
    for r in list_regions(base):
        # Compare with what was requested (the DEM grid may round the extent).
        r_lat = r.extra.get("request_lat", r.lat_center)
        r_lon = r.extra.get("request_lon", r.lon_center)
        r_w = float(r.extra.get("request_width_m", r.width_m))
        if r_lat is None or r_lon is None or r.source != source:
            continue
        if abs(r_w - width_m) > max(1.0, 0.02 * width_m):
            continue
        dy = (float(r_lat) - lat) * 111_320.0
        dx = (float(r_lon) - lon) * 111_320.0 * math.cos(math.radians(lat))
        if math.hypot(dx, dy) <= tol_m:
            return r
    return None
