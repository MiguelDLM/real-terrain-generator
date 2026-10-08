"""Layer-cake stratigraphy projected onto real topography.

A :class:`Stratigraphy` is a stack of layers (top to bottom) whose top surface passes
through ``datum_m`` at the terrain center and tilts with a planar dip. Intersecting that
stack with the DEM gives, for every vertex, the layer it exposes and its depth inside the
stack, which the material uses for banding. This reproduces the outcrop patterns of gently
deformed sedimentary sequences (Grand Canyon, Dolomites, mesas, cuestas); folds and faults
are out of scope.

Layers can be authored by the user or built from a Macrostrat stratigraphic column
(``/units`` endpoint: unit thickness and lithology proportions; best coverage in North America).
"""

from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple
import numpy as np

from .lithology import classify_lithology, profile_for


@dataclass
class StratLayer:
    """One stratigraphic layer."""
    name: str
    thickness_m: float
    rock: str                                   # Rock profile key (see lithology.ROCK_PROFILES)
    color: Optional[Tuple[float, float, float]] = None   # sRGB override
    hardness: Optional[float] = None            # Override of the rock's relative strength

    @property
    def effective_hardness(self) -> float:
        return profile_for(self.rock).hardness if self.hardness is None else self.hardness

    @property
    def effective_color(self) -> Tuple[float, float, float]:
        return profile_for(self.rock).color if self.color is None else self.color


@dataclass
class Stratigraphy:
    """Planar layer stack. ``dip_azimuth_deg`` is the downdip direction, clockwise from north."""
    layers: List[StratLayer]
    datum_m: float                  # Altitude of the stack's top surface at the terrain center
    dip_deg: float = 0.0
    dip_azimuth_deg: float = 0.0
    repeat: bool = False            # Cycle the stack below its base instead of extending the last layer

    @property
    def total_thickness_m(self) -> float:
        return sum(max(0.0, l.thickness_m) for l in self.layers)

    def structural_depth(self, x_m: np.ndarray, y_m: np.ndarray, z_m: np.ndarray) -> np.ndarray:
        """Depth below the stack's top surface, measured vertically (meters; negative = above it).

        ``x_m`` east and ``y_m`` north of the terrain center, ``z_m`` altitude.
        """
        az = math.radians(self.dip_azimuth_deg)
        along_dip = x_m * math.sin(az) + y_m * math.cos(az)
        top_surface = self.datum_m - math.tan(math.radians(self.dip_deg)) * along_dip
        return top_surface - z_m

    def layer_at_depth(self, depth_m: np.ndarray) -> np.ndarray:
        """Layer index for each depth (above the top -> 0, below the base -> last, or cycled)."""
        tops = np.cumsum([0.0] + [max(0.0, l.thickness_m) for l in self.layers])
        total = tops[-1]
        d = np.asarray(depth_m, dtype=np.float64)
        if self.repeat and total > 0:
            d = np.where(d > 0, np.mod(d, total), d)
        idx = np.searchsorted(tops, d, side="right") - 1
        return np.clip(idx, 0, len(self.layers) - 1).astype(np.int32)


def layers_from_macrostrat_units(units: Sequence[dict], min_thickness_m: float = 5.0) -> List[StratLayer]:
    """Build layers from Macrostrat ``/units?response=long`` records (ordered top to bottom).

    Thickness is the mean of min/max (or max when min is 0). The dominant lithology by
    proportion picks the rock; units without thickness are skipped.
    """
    layers: List[StratLayer] = []
    for u in sorted(units, key=lambda u: float(u.get("t_age") or 0.0)):
        t_min = float(u.get("min_thick") or 0.0)
        t_max = float(u.get("max_thick") or 0.0)
        thickness = (t_min + t_max) / 2.0 if t_min > 0 else t_max
        if thickness < min_thickness_m:
            continue
        liths = sorted(u.get("lith") or [], key=lambda l: -float(l.get("prop") or 0.0))
        rocks = classify_lithology(" ".join(l.get("name", "") for l in liths[:1]), u.get("strat_name_long", ""))
        rocks = rocks or classify_lithology(" ".join(l.get("type", "") + " " + l.get("class", "") for l in liths))
        layers.append(StratLayer(
            name=u.get("unit_name") or u.get("strat_name_long") or "Unit",
            thickness_m=thickness,
            rock=rocks[0][0] if rocks else "sandstone",
        ))
    return layers
