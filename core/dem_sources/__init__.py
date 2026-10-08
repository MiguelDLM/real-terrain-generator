"""DEM Providers registry."""

from .base import DEMProvider, ElevationGrid
from .aws_terrarium import AWSTerrariumProvider, decode_terrarium_elevation
from .opentopography import OpenTopographyProvider

__all__ = [
    "DEMProvider",
    "ElevationGrid",
    "AWSTerrariumProvider",
    "decode_terrarium_elevation",
    "OpenTopographyProvider",
]
