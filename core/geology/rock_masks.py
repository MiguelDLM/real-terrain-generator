"""Per-vertex rock assignment and loose-rock (boulder / cobble) distribution.

Combines the surface geology (map units or a layer stack) with the terrain analysis:

* Within a map unit that lists several rocks, harder rocks are placed where the terrain
  is steep and rugged and softer ones on gentle ground, since resistant rock forms
  scarps and weak rock (pyroclasts, marl, shale) weathers into smooth slopes
  (differential erosion; Selby 1980). A low-frequency noise keeps the patches organic.
* Loose rocks come from three geomorphic settings, each with its own size and rounding:
  - scree / talus below cliffs: angular, sized by the parent rock's jointing;
  - stream beds: rounded cobbles and boulders (transport rounds clasts), larger where
    flow and gradient (stream power) are higher;
  - bedrock outcrops on steep, rugged, hard rock: few, large, angular blocks.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Optional
import numpy as np

from ..dem_sources.base import ElevationGrid
from ..mesh_builder import TerrainAnalysis
from .lithology import ROCK_PROFILES, profile_for, rock_style_parameters
from .macrostrat import GeologyMap
from .stratigraphy import Stratigraphy


@dataclass
class RockLayers:
    """Per-vertex rock data (all arrays have one value per grid vertex, row-major)."""
    rock_keys: List[str]            # Palette: rock_id -> rock profile key
    rock_id: np.ndarray             # int32
    color: np.ndarray               # (N, 3) sRGB of the rock (or layer override)
    color_alt: np.ndarray           # (N, 3) sRGB secondary tone
    fracture: np.ndarray            # (N, 4) blocky, columnar, layering, rounded (shader weights)
    surface: np.ndarray             # (N, 4) vesicular, karst, granular, roughness
    joint_m: np.ndarray             # float32, joint / block spacing in meters
    bedding_m: np.ndarray           # float32, layer thickness in meters
    hardness: np.ndarray            # float32 0..1
    strata_depth_m: np.ndarray      # float32, depth inside the layer stack (minus altitude without one: horizontal bands)
    boulder_density: np.ndarray     # float32 0..1
    clast_size_m: np.ndarray        # float32, typical loose rock size at each vertex
    clast_rounding: np.ndarray      # float32 0 (angular) .. 1 (well rounded)
    unit_id: np.ndarray             # int32, map unit or layer index (-1 = none)


def _smooth(field: np.ndarray, passes: int) -> np.ndarray:
    out = field.astype(np.float32)
    for _ in range(passes):
        p = np.pad(out, 1, mode="edge")
        out = (p[:-2, 1:-1] + p[2:, 1:-1] + p[1:-1, :-2] + p[1:-1, 2:] + 4.0 * p[1:-1, 1:-1]) / 8.0
    return out


def _value_noise(shape, cells: int, seed: int) -> np.ndarray:
    """Smooth 0..1 noise: a coarse random lattice upsampled bilinearly."""
    rows, cols = shape
    rng = np.random.default_rng(seed)
    lattice = rng.random((cells + 1, cells + 1)).astype(np.float32)
    y = np.linspace(0, cells, rows)
    x = np.linspace(0, cells, cols)
    y0 = np.minimum(y.astype(int), cells - 1)
    x0 = np.minimum(x.astype(int), cells - 1)
    fy = (y - y0)[:, None]
    fx = (x - x0)[None, :]
    a = lattice[y0][:, x0]
    b = lattice[y0][:, x0 + 1]
    c = lattice[y0 + 1][:, x0]
    d = lattice[y0 + 1][:, x0 + 1]
    return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy


def _smoothstep(x, lo, hi):
    t = np.clip((x - lo) / max(1e-6, hi - lo), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def assign_rocks(
    grid: ElevationGrid,
    analysis: TerrainAnalysis,
    geology: Optional[GeologyMap] = None,
    stratigraphy: Optional[Stratigraphy] = None,
    default_rock: str = "volcanic",
    seed: int = 0,
) -> RockLayers:
    """Decide the rock at every vertex and where loose rocks lie.

    ``stratigraphy`` takes precedence over ``geology``; without either, ``default_rock``
    covers the whole terrain.
    """
    elev = grid.elevation
    rows, cols = elev.shape
    n = rows * cols
    slope_n = np.clip(analysis.slope_deg / 60.0, 0.0, 1.0)
    rough = analysis.static_masks["RTG_Roughness"].reshape(elev.shape)
    exposure = _smooth(0.6 * slope_n + 0.4 * rough, 3) + 0.35 * (_value_noise(elev.shape, 6, seed) - 0.5)

    palette: List[str] = []
    slot: Dict[str, int] = {}

    def rock_index(key: str) -> int:
        if key not in slot:
            slot[key] = len(palette)
            palette.append(key)
        return slot[key]

    rock_id = np.zeros(elev.shape, dtype=np.int32)
    unit_id = np.full(elev.shape, -1, dtype=np.int32)
    strata_depth = -elev.astype(np.float32)
    color = np.zeros(elev.shape + (3,), dtype=np.float32)
    hardness = np.zeros(elev.shape, dtype=np.float32)

    if stratigraphy and stratigraphy.layers:
        x = np.linspace(-grid.width_m / 2.0, grid.width_m / 2.0, cols)[None, :]
        y = np.linspace(grid.height_m / 2.0, -grid.height_m / 2.0, rows)[:, None]
        depth = stratigraphy.structural_depth(x, y, elev)
        layer = stratigraphy.layer_at_depth(depth)
        strata_depth = depth.astype(np.float32)
        unit_id = layer
        for i, lay in enumerate(stratigraphy.layers):
            sel = layer == i
            rock_id[sel] = rock_index(lay.rock)
            color[sel] = lay.effective_color
            hardness[sel] = lay.effective_hardness
    elif geology and geology.units:
        unit_id = geology.unit_index.astype(np.int32)
        for u_idx, unit in enumerate(geology.units):
            sel = unit_id == u_idx
            if not np.any(sel):
                continue
            rocks = unit.rocks or [(default_rock, 1.0)]
            # Softest rocks on the least exposed ground, hardest on the most exposed
            rocks = sorted(rocks, key=lambda r: profile_for(r[0]).hardness)
            ranks = np.argsort(np.argsort(exposure[sel])) / max(1, int(sel.sum()) - 1)
            bounds = np.cumsum([w for _, w in rocks])
            choice = np.minimum(np.searchsorted(bounds, ranks, side="left"), len(rocks) - 1)
            ids = np.array([rock_index(k) for k, _ in rocks], dtype=np.int32)
            rock_id[sel] = ids[choice]
    else:
        rock_id[:] = rock_index(default_rock)

    if not (stratigraphy and stratigraphy.layers):
        profiles = [profile_for(k) for k in palette]
        color = np.array([p.color for p in profiles], dtype=np.float32)[rock_id]
        hardness = np.array([p.hardness for p in profiles], dtype=np.float32)[rock_id]

    profiles = [profile_for(k) for k in palette]
    styles = [rock_style_parameters(p) for p in profiles]
    if stratigraphy and stratigraphy.layers:
        color_alt = color * 0.8
    else:
        color_alt = np.array([p.color_alt for p in profiles], dtype=np.float32)[rock_id]
    fracture = np.array([st[0] for st in styles], dtype=np.float32)[rock_id]
    surface = np.array([st[1] for st in styles], dtype=np.float32)[rock_id]
    joint_m = np.array([st[2] for st in styles], dtype=np.float32)[rock_id]
    bedding_m = np.array([st[3] for st in styles], dtype=np.float32)[rock_id]
    if stratigraphy and stratigraphy.layers:
        # Bedding scales with layer thickness so thin layers show fine laminae
        thick = np.array([max(0.05, l.thickness_m) for l in stratigraphy.layers], dtype=np.float32)
        bedding_m = np.minimum(bedding_m, thick[unit_id] / 4.0)

    clast_parent = np.array([profile_for(k).clast_size_m for k in palette], dtype=np.float32)[rock_id]
    rounded_parent = np.array([profile_for(k).weathering == "rounded" for k in palette], dtype=np.float32)[rock_id]

    # Loose rock settings
    talus = analysis.static_masks["RTG_Talus"].reshape(elev.shape)
    flow = analysis.static_masks["RTG_Water_Flow"].reshape(elev.shape)
    talus_density = talus * (0.25 + 0.75 * hardness)

    channel = _smoothstep(flow, 0.35, 0.75) * (1.0 - _smoothstep(analysis.slope_deg, 12.0, 25.0))
    stream_power = np.clip(flow * np.tan(np.radians(np.clip(analysis.slope_deg, 0.5, 30.0))) * 4.0, 0.0, 1.0)
    river_density = channel * (0.4 + 0.6 * hardness)
    river_size = 0.1 + 0.9 * stream_power            # Gravel to ~1 m boulders

    outcrop_density = 0.25 * _smoothstep(slope_n * rough * hardness, 0.15, 0.45)

    stacked = np.stack([talus_density, river_density, outcrop_density])
    dominant = np.argmax(stacked, axis=0)
    density = np.clip(stacked.max(axis=0), 0.0, 1.0)
    size = np.choose(dominant, [clast_parent, np.minimum(river_size, np.maximum(0.1, clast_parent)), clast_parent * 1.5])
    rounding = np.choose(dominant, [0.1 + 0.5 * rounded_parent, np.full_like(size, 0.9), 0.1 + 0.6 * rounded_parent])
    # Loose material (sand, ash, pumice) has no blocks to shed
    density = density * _smoothstep(clast_parent, 0.05, 0.3)

    return RockLayers(
        rock_keys=palette,
        rock_id=rock_id.ravel(),
        color=color.reshape(n, 3),
        color_alt=color_alt.reshape(n, 3).astype(np.float32),
        fracture=fracture.reshape(n, 4),
        surface=surface.reshape(n, 4),
        joint_m=joint_m.ravel(),
        bedding_m=bedding_m.ravel().astype(np.float32),
        hardness=hardness.ravel().astype(np.float32),
        strata_depth_m=strata_depth.ravel().astype(np.float32),
        boulder_density=density.ravel().astype(np.float32),
        clast_size_m=size.ravel().astype(np.float32),
        clast_rounding=rounding.ravel().astype(np.float32),
        unit_id=unit_id.ravel().astype(np.int32),
    )


__all__ = ["RockLayers", "assign_rocks", "plan_surface_terms", "SURFACE_TERMS", "ROCK_PROFILES"]


# Shader terms, in the order of RockLayers.fracture (RGBA) then RockLayers.surface (RGB)
SURFACE_TERMS = ("blocky", "columnar", "layered", "rounded", "vesicular", "karst", "granular")


def plan_surface_terms(rocks: RockLayers, min_weight: float = 0.05, min_share: float = 0.002) -> tuple:
    """Shader terms a terrain actually needs, so the material only evaluates those.

    A term is kept when its weight exceeds ``min_weight`` on at least ``min_share`` of the
    vertices. A single-lithology terrain (common on plains) typically needs one or two.
    """
    weights = np.concatenate([rocks.fracture, rocks.surface[:, :3]], axis=1)
    share = (weights > min_weight).mean(axis=0)
    return tuple(t for t, s in zip(SURFACE_TERMS, share) if s >= min_share)
