"""Mapbox Terrain-RGB DEM Provider (Architecture & Future Implementation Notes).

Mapbox provides globally consistent, pre-processed elevation tiles in Web Mercator format.

Tile URL Template:
    https://api.mapbox.com/v4/mapbox.terrain-rgb/{z}/{x}/{y}.pngraw?access_token={token}

Decoding Formula:
    height_meters = -10000 + ((R * 256 * 256 + G * 256 + B) * 0.1)

Precision & Range:
    Vertical precision: 0.1 meters (10 cm)
    Elevation range: -10,000 m to +16,777.2 m
    Resolution: up to zoom level 15 (~4.8 m/pixel)

Authentication & Pricing:
    Requires Mapbox Access Token.
    Free tier includes 200,000 vector/raster tile requests per month.
"""

from __future__ import annotations
from pathlib import Path
from typing import Optional
from ..geo import BoundingBox
from .base import DEMProvider, ElevationGrid


class MapboxRGBProvider(DEMProvider):
    """Provider for Mapbox Terrain-RGB elevation tiles."""

    def __init__(self, access_token: str = ""):
        self.access_token = access_token

    @property
    def name(self) -> str:
        return "Mapbox Terrain-RGB"

    @property
    def requires_api_key(self) -> bool:
        return True

    @property
    def attribution(self) -> str:
        return "Mapbox / OpenStreetMap contributors"

    def fetch_elevation_grid(
        self,
        bbox: BoundingBox,
        zoom: int = 12,
        target_resolution: Optional[int] = None,
        cache_dir: Optional[Path] = None,
    ) -> ElevationGrid:
        if not self.access_token:
            raise ValueError(
                "Mapbox requires an access token. "
                "Register at https://mapbox.com and paste your token in add-on preferences."
            )
        raise NotImplementedError(
            "Mapbox Terrain-RGB is planned for a future release. "
            "Use AWS Terrain Tiles (Terrarium) for free, key-less elevation data."
        )
