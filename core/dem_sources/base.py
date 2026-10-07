"""Base classes and data structures for Digital Elevation Model (DEM) providers."""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Any
import numpy as np

from ..geo import BoundingBox


@dataclass
class ElevationGrid:
    """Represents a 2D digital elevation model grid with spatial metadata."""
    elevation: np.ndarray       # 2D float32 array of elevation values in meters
    bbox: BoundingBox           # Geographic boundary
    width_m: float              # Real-world metric width (East-West)
    height_m: float             # Real-world metric height (North-South)
    min_elevation_m: float      # Lowest point in meters
    max_elevation_m: float      # Highest point in meters
    provider_name: str          # Name of the DEM service used
    attribution: str            # Required license/credit attribution string
    metadata: Dict[str, Any]    # Extra provider metadata

    @property
    def elevation_range_m(self) -> float:
        """Total vertical relief (max - min) in meters."""
        return max(0.001, self.max_elevation_m - self.min_elevation_m)

    @property
    def shape(self) -> tuple[int, int]:
        """Array dimensions (rows, cols)."""
        return self.elevation.shape


class DEMProvider(ABC):
    """Abstract base class for DEM data sources."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable provider name."""
        pass

    @property
    @abstractmethod
    def requires_api_key(self) -> bool:
        """True if registration/API key is required."""
        pass

    @property
    @abstractmethod
    def attribution(self) -> str:
        """Data attribution statement."""
        pass

    @abstractmethod
    def fetch_elevation_grid(
        self,
        bbox: BoundingBox,
        zoom: int = 12,
        target_resolution: Optional[int] = None,
        cache_dir: Optional[Path] = None,
    ) -> ElevationGrid:
        """Fetch elevation data for the given bounding box and return an ElevationGrid."""
        pass
