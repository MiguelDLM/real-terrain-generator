"""AWS Terrain Tiles (Terrarium format) DEM Provider.

Fetches 256x256 elevation tiles from the public AWS Open Data S3 bucket:
https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png

Decodes elevation using the Terrarium formula:
    height_meters = (R * 256 + G + B / 256) - 32768

Requires ZERO API keys or registration.
"""

from __future__ import annotations
import math
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional, Tuple, List
import numpy as np

from ..geo import (
    BoundingBox,
    get_tiles_for_bbox,
    tile_bounds,
    haversine_distance_m,
    lat_lon_to_tile,
    clamp
)
from ..png_io import decode_png_rgb
from .base import DEMProvider, ElevationGrid

DEFAULT_USER_AGENT = "RealTerrainGenerator/0.1.0 (Blender Extension; +https://github.com/user/real-terrain-generator)"
TERRARIUM_URL_TEMPLATE = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"


def decode_terrarium_elevation(rgb_array: np.ndarray) -> np.ndarray:
    """Convert an (H, W, 3) uint8 Terrarium RGB image into float32 elevation in meters.
    
    Formula: (R * 256 + G + B / 256) - 32768
    """
    r = rgb_array[:, :, 0].astype(np.float32)
    g = rgb_array[:, :, 1].astype(np.float32)
    b = rgb_array[:, :, 2].astype(np.float32)
    
    elevation = (r * 256.0 + g + b / 256.0) - 32768.0
    return elevation


def resample_grid_bilinear(grid: np.ndarray, target_size: int) -> np.ndarray:
    """Resample a 2D float array to (target_size, target_size) using bilinear interpolation in pure NumPy."""
    in_h, in_w = grid.shape
    if in_h == target_size and in_w == target_size:
        return grid.copy()

    y_indices = np.linspace(0, in_h - 1, target_size)
    x_indices = np.linspace(0, in_w - 1, target_size)

    y0 = np.floor(y_indices).astype(int)
    y1 = np.clip(y0 + 1, 0, in_h - 1)
    x0 = np.floor(x_indices).astype(int)
    x1 = np.clip(x0 + 1, 0, in_w - 1)

    wy1 = (y_indices - y0)[:, np.newaxis]
    wy0 = 1.0 - wy1

    wx1 = (x_indices - x0)[np.newaxis, :]
    wx0 = 1.0 - wx1

    top = wy0 * grid[y0, :][:, x0] * wx0 + wy0 * grid[y0, :][:, x1] * wx1
    bot = wy1 * grid[y1, :][:, x0] * wx0 + wy1 * grid[y1, :][:, x1] * wx1
    return (top + bot).astype(np.float32)


class AWSTerrariumProvider(DEMProvider):
    """DEM Provider backed by AWS Terrain Tiles (Terrarium encoding)."""

    def __init__(self, user_agent: str = DEFAULT_USER_AGENT):
        self._user_agent = user_agent

    @property
    def name(self) -> str:
        return "AWS Terrain Tiles (Terrarium)"

    @property
    def requires_api_key(self) -> bool:
        return False

    @property
    def attribution(self) -> str:
        return "Mapzen / Nextzen Terrain Tiles via AWS Open Data (USGS, SRTM, ETOPO1)"

    def _fetch_tile_bytes(self, z: int, x: int, y: int, cache_dir: Optional[Path] = None) -> bytes:
        """Fetch raw PNG bytes for a single tile, consulting cache first."""
        cache_file = None
        if cache_dir:
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file = cache_dir / f"terrarium_{z}_{x}_{y}.png"
            if cache_file.exists():
                return cache_file.read_bytes()

        url = TERRARIUM_URL_TEMPLATE.format(z=z, x=x, y=y)
        req = urllib.request.Request(url, headers={"User-Agent": self._user_agent})
        
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                tile_bytes = response.read()
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Failed to fetch tile {z}/{x}/{y}: HTTP {e.code} - {e.reason}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"Network error fetching tile {z}/{x}/{y}: {e.reason}")

        if cache_file:
            cache_file.write_bytes(tile_bytes)

        return tile_bytes

    def fetch_elevation_grid(
        self,
        bbox: BoundingBox,
        zoom: int = 12,
        target_resolution: Optional[int] = None,
        cache_dir: Optional[Path] = None,
    ) -> ElevationGrid:
        """Download and stitch the required tiles to cover the bounding box."""
        tiles, (min_x, max_x, min_y, max_y) = get_tiles_for_bbox(bbox, zoom)
        tiles_w = (max_x - min_x) + 1
        tiles_h = (max_y - min_y) + 1

        tile_pixel_size = 256
        canvas_h = tiles_h * tile_pixel_size
        canvas_w = tiles_w * tile_pixel_size
        stitched_elevation = np.zeros((canvas_h, canvas_w), dtype=np.float32)

        for z, x, y in tiles:
            tile_bytes = self._fetch_tile_bytes(z, x, y, cache_dir=cache_dir)
            rgb = decode_png_rgb(tile_bytes)
            tile_elev = decode_terrarium_elevation(rgb)

            col_offset = (x - min_x) * tile_pixel_size
            row_offset = (y - min_y) * tile_pixel_size
            stitched_elevation[row_offset:row_offset + tile_pixel_size, col_offset:col_offset + tile_pixel_size] = tile_elev

        # Precise crop to bounding box
        # Calculate fractional pixel coordinates inside the stitched canvas
        n = 2.0 ** zoom
        # Northwest (top-left) tile origin in Web Mercator
        nw_tile_x = min_x
        nw_tile_y = min_y

        def lat_lon_to_pixel(lat: float, lon: float) -> Tuple[float, float]:
            lon_clamped = clamp(lon, -180.0, 180.0)
            lat_clamped = clamp(lat, -85.05112878, 85.05112878)
            x_tile_f = (lon_clamped + 180.0) / 360.0 * n
            lat_rad = math.radians(lat_clamped)
            y_tile_f = (1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n

            px = (x_tile_f - nw_tile_x) * tile_pixel_size
            py = (y_tile_f - nw_tile_y) * tile_pixel_size
            return px, py

        px_left, py_top = lat_lon_to_pixel(bbox.max_lat, bbox.min_lon)
        px_right, py_bottom = lat_lon_to_pixel(bbox.min_lat, bbox.max_lon)

        c_left = int(clamp(math.floor(px_left), 0, canvas_w - 1))
        c_right = int(clamp(math.ceil(px_right), c_left + 1, canvas_w))
        c_top = int(clamp(math.floor(py_top), 0, canvas_h - 1))
        c_bottom = int(clamp(math.ceil(py_bottom), c_top + 1, canvas_h))

        cropped = stitched_elevation[c_top:c_bottom, c_left:c_right]

        if target_resolution and (cropped.shape[0] != target_resolution or cropped.shape[1] != target_resolution):
            elevation_final = resample_grid_bilinear(cropped, target_resolution)
        else:
            elevation_final = cropped

        # Calculate metric width & height
        center_lat, center_lon = bbox.center
        width_m = haversine_distance_m(center_lat, bbox.min_lon, center_lat, bbox.max_lon)
        height_m = haversine_distance_m(bbox.min_lat, center_lon, bbox.max_lat, center_lon)

        min_elev = float(np.min(elevation_final))
        max_elev = float(np.max(elevation_final))

        return ElevationGrid(
            elevation=elevation_final,
            bbox=bbox,
            width_m=width_m,
            height_m=height_m,
            min_elevation_m=min_elev,
            max_elevation_m=max_elev,
            provider_name=self.name,
            attribution=self.attribution,
            metadata={
                "zoom": zoom,
                "tiles_count": len(tiles),
                "tile_grid": (tiles_w, tiles_h),
            }
        )
