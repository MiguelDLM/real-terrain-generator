"""USGS 3D Elevation Program (3DEP) Provider (Architecture & Future Implementation Notes).

The USGS 3DEP program provides high-resolution elevation data across the United States:
- 1 meter LiDAR bare-earth DEMs (extreme fine detail: creeks, road cuts, rock formations)
- 1/9 arc-second (~3 meters)
- 1/3 arc-second (~10 meters)
- 1 arc-second (~30 meters)

Coverage:
    United States and territories only.

APIs & Endpoints:
    1. The National Map (TNM) Access API:
       https://tnmaccess.nationalmap.gov/api/v1/products
    2. USGS 3DEP Elevation Web Coverage Service (WCS):
       https://elevation.nationalmap.gov/arcgis/services/3DEPElevation/ImageServer/WCSServer
    3. USGS Point Elevation Query Service:
       https://epqs.nationalmap.gov/v1/json?x={lon}&y={lat}&units=Meters

Authentication:
    Free and public domain (US Government work). No API key required.
"""

from __future__ import annotations
from pathlib import Path
from typing import Optional
from ..geo import BoundingBox
from .base import DEMProvider, ElevationGrid


class USGS3DEPProvider(DEMProvider):
    """Provider for USGS 3DEP High-Resolution LiDAR elevation in the USA."""

    @property
    def name(self) -> str:
        return "USGS 3DEP (US LiDAR 1m-10m)"

    @property
    def requires_api_key(self) -> bool:
        return False

    @property
    def attribution(self) -> str:
        return "U.S. Geological Survey (USGS 3DEP)"

    def fetch_elevation_grid(
        self,
        bbox: BoundingBox,
        zoom: int = 12,
        target_resolution: Optional[int] = None,
        cache_dir: Optional[Path] = None,
    ) -> ElevationGrid:
        raise NotImplementedError(
            "USGS 3DEP direct WCS ingestion is planned for a future update. "
            "AWS Terrain Tiles already incorporates USGS 3DEP data across North America at zoom 14-15."
        )
