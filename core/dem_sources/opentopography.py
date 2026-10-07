"""OpenTopography Global DEM API Provider (Architecture & Future Implementation Notes).

OpenTopography provides high-grade academic and scientific DEM raster datasets:
- Copernicus GLO-30 (COP30) - 30m global resolution (radar derived, modern, high accuracy)
- ALOS World 3D (AW3D30) - 30m global surface model (JAXA)
- SRTM GL1 (30m) & SRTM GL3 (90m) - NASA Shuttle Radar Topography Mission
- NASADEM - Reprocessed SRTM with ASTER GDEM fill
- High-Resolution LiDAR (1m) - Available for US, New Zealand, and parts of Europe

API Endpoint:
    https://portal.opentopography.org/API/globaldem

Query Parameters:
    demtype: "COP30" | "COP90" | "AW3D30" | "SRTMGL1" | "SRTMGL3" | "NASADEM"
    south: float (min_lat)
    north: float (max_lat)
    west:  float (min_lon)
    east:  float (max_lon)
    outputFormat: "GTiff" | "AAIGrid"
    API_Key: string (free account registration at https://portal.opentopography.org)

Advantages over Tile-based systems:
    1. Returns exact geographic bounding box without manual tile stitching or cropping.
    2. Data is delivered directly as true floating-point elevation values in meters (GeoTIFF / AAIGrid).
    3. Access to Copernicus 30m, which avoids voids in steep mountainous terrains.
"""

from __future__ import annotations
from pathlib import Path
from typing import Optional
from ..geo import BoundingBox
from .base import DEMProvider, ElevationGrid


class OpenTopographyProvider(DEMProvider):
    """Provider for OpenTopography Global DEM API (COP30, ALOS, SRTM)."""

    def __init__(self, api_key: str = "", dem_type: str = "COP30"):
        self.api_key = api_key
        self.dem_type = dem_type

    @property
    def name(self) -> str:
        return f"OpenTopography ({self.dem_type})"

    @property
    def requires_api_key(self) -> bool:
        return True

    @property
    def attribution(self) -> str:
        return "OpenTopography / Copernicus GLO-30 / NASA / JAXA"

    def fetch_elevation_grid(
        self,
        bbox: BoundingBox,
        zoom: int = 12,
        target_resolution: Optional[int] = None,
        cache_dir: Optional[Path] = None,
    ) -> ElevationGrid:
        if not self.api_key:
            raise ValueError(
                "OpenTopography requires a free API key. "
                "Register at https://portal.opentopography.org/ and provide the key in add-on preferences."
            )
        raise NotImplementedError(
            "OpenTopography integration is architected and ready for future extension. "
            "Please use the AWS Terrain Tiles (Terrarium) provider for zero-key generation."
        )
