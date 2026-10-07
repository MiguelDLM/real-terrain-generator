"""DEM Providers registry."""

from .base import DEMProvider, ElevationGrid
from .aws_terrarium import AWSTerrariumProvider, decode_terrarium_elevation
from .opentopography import OpenTopographyProvider
from .mapbox_rgb import MapboxRGBProvider
from .usgs_3dep import USGS3DEPProvider

__all__ = [
    "DEMProvider",
    "ElevationGrid",
    "AWSTerrariumProvider",
    "decode_terrarium_elevation",
    "OpenTopographyProvider",
    "MapboxRGBProvider",
    "USGS3DEPProvider",
]
