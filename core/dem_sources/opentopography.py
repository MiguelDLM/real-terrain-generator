"""OpenTopography Global DEM API Provider (Full Implementation).

Queries the OpenTopography Global DEM REST API:
https://portal.opentopography.org/API/globaldem

Available Datasets:
- COP30: Copernicus GLO-30 Global DSM (30m, high precision, radar-derived by ESA)
- COP90: Copernicus GLO-90 Global DSM (90m)
- AW3D30: ALOS World 3D (30m DSM by JAXA)
- SRTMGL1: NASA SRTM 1 arc-second (30m)
- SRTMGL3: NASA SRTM 3 arc-second (90m)
- NASADEM: NASADEM Global DEM (30m)

Uses the AAIGrid (Arc ASCII Grid) format for exact floating-point metric elevation
with zero external C-dependencies (pure Python + NumPy).
"""

from __future__ import annotations
import io
import math
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional, Tuple, Dict, Any
import numpy as np

from ..geo import BoundingBox, haversine_distance_m
from .base import DEMProvider, ElevationGrid
from .aws_terrarium import resample_grid_bilinear

OPENTOPO_URL = "https://portal.opentopography.org/API/globaldem"
DEFAULT_USER_AGENT = "RealTerrainGenerator/0.1.0 (Blender Extension; +https://github.com/user/real-terrain-generator)"

SUPPORTED_DATASETS = {
    "COP30": "Copernicus DEM GLO-30 (30m)",
    "COP90": "Copernicus DEM GLO-90 (90m)",
    "AW3D30": "ALOS World 3D (30m)",
    "SRTMGL1": "NASA SRTM 1 arc-sec (30m)",
    "SRTMGL3": "NASA SRTM 3 arc-sec (90m)",
    "NASADEM": "NASADEM Global (30m)",
}


def parse_aaigrid(ascii_text: str) -> Tuple[np.ndarray, Dict[str, Any]]:
    """Parse an Arc ASCII Grid (AAIGrid) format string into a 2D float32 NumPy elevation array.
    
    Header structure:
        ncols         <int>
        nrows         <int>
        xllcorner     <float> (or xllcenter)
        yllcorner     <float> (or yllcenter)
        cellsize      <float>
        NODATA_value  <float>
    """
    lines = ascii_text.strip().splitlines()
    header: Dict[str, Any] = {}
    data_start_line = 0

    for idx, line in enumerate(lines):
        parts = line.strip().split()
        if len(parts) == 2 and parts[0].upper() in (
            "NCOLS", "NROWS", "XLLCORNER", "YLLCORNER",
            "XLLCENTER", "YLLCENTER", "CELLSIZE", "NODATA_VALUE"
        ):
            key = parts[0].upper()
            val_str = parts[1]
            header[key] = float(val_str) if "." in val_str else int(val_str)
            data_start_line = idx + 1
        else:
            break

    if "NCOLS" not in header or "NROWS" not in header:
        raise ValueError("Invalid AAIGrid header: missing NCOLS or NROWS")

    ncols = int(header["NCOLS"])
    nrows = int(header["NROWS"])

    data_string = " ".join(lines[data_start_line:])
    grid = np.fromstring(data_string, sep=" ", dtype=np.float32)

    if grid.size != (nrows * ncols):
        raise ValueError(
            f"AAIGrid dimension mismatch: expected {nrows}x{ncols} = {nrows * ncols} values, got {grid.size}"
        )

    grid = grid.reshape((nrows, ncols))

    # Handle NODATA_value
    nodata_val = float(header.get("NODATA_VALUE", -9999.0))
    is_nodata = np.isclose(grid, nodata_val) | (grid < -10000.0) | np.isnan(grid)

    if np.any(is_nodata):
        valid_values = grid[~is_nodata]
        fill_val = float(np.min(valid_values)) if valid_values.size > 0 else 0.0
        grid[is_nodata] = fill_val

    return grid, header


class OpenTopographyProvider(DEMProvider):
    """DEM Provider backed by the OpenTopography Global DEM API."""

    def __init__(self, api_key: str = "", dem_type: str = "COP30", user_agent: str = DEFAULT_USER_AGENT):
        self.api_key = api_key.strip()
        self.dem_type = dem_type.strip().upper()
        self._user_agent = user_agent

        if self.dem_type not in SUPPORTED_DATASETS:
            raise ValueError(
                f"Unsupported DEM type '{self.dem_type}'. Supported: {list(SUPPORTED_DATASETS.keys())}"
            )

    @property
    def name(self) -> str:
        dataset_label = SUPPORTED_DATASETS.get(self.dem_type, self.dem_type)
        return f"OpenTopography ({dataset_label})"

    @property
    def requires_api_key(self) -> bool:
        return True

    @property
    def attribution(self) -> str:
        return f"OpenTopography / {self.dem_type} (ESA / NASA / JAXA)"

    def _build_request_url(self, bbox: BoundingBox) -> str:
        params = {
            "demtype": self.dem_type,
            "south": f"{bbox.min_lat:.6f}",
            "north": f"{bbox.max_lat:.6f}",
            "west": f"{bbox.min_lon:.6f}",
            "east": f"{bbox.max_lon:.6f}",
            "outputFormat": "AAIGrid",
            "API_Key": self.api_key,
        }
        return f"{OPENTOPO_URL}?{urllib.parse.urlencode(params)}"

    def _fetch_aaigrid_text(self, bbox: BoundingBox, cache_dir: Optional[Path] = None) -> str:
        """Fetch AAIGrid ASCII response from OpenTopography API or cache."""
        cache_file = None
        if cache_dir:
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_filename = (
                f"opentopo_{self.dem_type}_{bbox.min_lat:.4f}_{bbox.max_lat:.4f}_"
                f"{bbox.min_lon:.4f}_{bbox.max_lon:.4f}.asc"
            )
            cache_file = cache_dir / cache_filename
            if cache_file.exists():
                return cache_file.read_text(encoding="utf-8")

        if not self.api_key:
            raise ValueError(
                "OpenTopography requires an API Key. "
                "Get your free key at https://portal.opentopography.org/ and enter it in Preferences > Add-ons > Real Terrain Generator."
            )

        url = self._build_request_url(bbox)
        req = urllib.request.Request(url, headers={"User-Agent": self._user_agent})

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            error_body = ""
            try:
                error_body = e.read().decode("utf-8", errors="ignore")[:300]
            except Exception:
                pass

            if e.code == 401 or "API_Key" in error_body or "unauthorized" in error_body.lower():
                raise RuntimeError(
                    f"OpenTopography Authentication Failed (HTTP {e.code}): Invalid or missing API Key. "
                    "Check your key at https://portal.opentopography.org/myopentopo"
                )
            elif e.code == 400:
                raise RuntimeError(
                    f"OpenTopography Bad Request (HTTP 400): {error_body or e.reason}"
                )
            else:
                raise RuntimeError(f"OpenTopography request failed with HTTP {e.code}: {e.reason} ({error_body})")
        except urllib.error.URLError as e:
            raise RuntimeError(f"Network error connecting to OpenTopography: {e.reason}")

        # Check if response returned HTML error instead of AAIGrid
        if data.strip().startswith("<!DOCTYPE") or "<html" in data[:100].lower():
            raise RuntimeError(
                f"OpenTopography returned an unexpected HTML response instead of DEM data: {data[:200]}"
            )

        if cache_file:
            cache_file.write_text(data, encoding="utf-8")

        return data

    def fetch_elevation_grid(
        self,
        bbox: BoundingBox,
        zoom: int = 12,
        target_resolution: Optional[int] = None,
        cache_dir: Optional[Path] = None,
    ) -> ElevationGrid:
        """Fetch elevation grid for the requested bounding box from OpenTopography."""
        ascii_text = self._fetch_aaigrid_text(bbox, cache_dir=cache_dir)
        elevation_arr, header = parse_aaigrid(ascii_text)

        if target_resolution and (elevation_arr.shape[0] != target_resolution or elevation_arr.shape[1] != target_resolution):
            elevation_final = resample_grid_bilinear(elevation_arr, target_resolution)
        else:
            elevation_final = elevation_arr

        # Calculate metric real-world dimensions
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
                "dem_type": self.dem_type,
                "header": header,
                "original_shape": elevation_arr.shape,
            }
        )
