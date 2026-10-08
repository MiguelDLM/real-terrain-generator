"""Water bodies on real terrain: surface mesh, depth and flow direction.

Instead of a flat plane over the whole terrain, the water surface is built from the
terrain grid itself: only cells that are (nearly) under water are kept, every vertex knows
its depth (water level minus bed elevation), and a margin of cells extends under the shore
so the terrain hides the edge. Depth drives the water material (clear and bed-tinted in
the shallows, darker and opaque in deep water, foam at the shoreline), and the flow
direction orients and advects the ripples.

DEMs such as SRTM record the water surface, not the bed: the lowest cells of a river or
reservoir sit at water level, which makes the level easy to estimate.
"""

from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Optional
import numpy as np

from .dem_sources.base import ElevationGrid
from .mesh_builder import compute_scaled_vertices


@dataclass
class WaterMesh:
    vertices: np.ndarray        # (N, 3) object space
    faces: np.ndarray           # (M, 4) quads
    uvs: np.ndarray             # (N, 2) planar, 0..1 over the terrain
    depth_m: np.ndarray         # (N,) water level minus terrain elevation (negative under the shore margin)
    position_m: np.ndarray      # (N, 3) real meters: x east, y north (from center), z = water level


def estimate_water_level(elevation: np.ndarray, margin_m: float = 1.0) -> float:
    """Water level just above the lowest ground (0.5th percentile skips isolated DEM pits)."""
    return float(np.percentile(elevation, 0.5)) + margin_m


def water_face_mask(elevation: np.ndarray, level_m: float, margin_cells: int = 1) -> np.ndarray:
    """(rows-1, cols-1) mask of grid quads to keep: any corner under water, grown by ``margin_cells``."""
    wet = elevation < level_m
    faces = wet[:-1, :-1] | wet[1:, :-1] | wet[:-1, 1:] | wet[1:, 1:]
    for _ in range(max(0, margin_cells)):
        p = np.pad(faces, 1)
        faces = p[1:-1, 1:-1] | p[:-2, 1:-1] | p[2:, 1:-1] | p[1:-1, :-2] | p[1:-1, 2:]
    return faces


def build_water_mesh(
    grid: ElevationGrid,
    level_m: float,
    horizontal_scale: float = 1.0,
    vertical_scale: float = 1.0,
    center_origin: bool = True,
    margin_cells: int = 1,
) -> Optional[WaterMesh]:
    """Water surface over the submerged part of ``grid``, in the same object space as the terrain mesh."""
    rows, cols = grid.shape
    face_mask = water_face_mask(grid.elevation, level_m, margin_cells)
    if not face_mask.any():
        return None

    flat = ElevationGrid(
        elevation=np.full(grid.shape, level_m, dtype=np.float32), bbox=grid.bbox,
        width_m=grid.width_m, height_m=grid.height_m,
        min_elevation_m=grid.min_elevation_m, max_elevation_m=grid.max_elevation_m,
        provider_name=grid.provider_name, attribution=grid.attribution, metadata={},
    )
    all_verts = compute_scaled_vertices(flat, vertical_scale, horizontal_scale, center_origin, base_elevation_zero=True)

    r, c = np.nonzero(face_mask)
    quads = np.stack([r * cols + c, (r + 1) * cols + c, (r + 1) * cols + c + 1, r * cols + c + 1], axis=1)
    used = np.unique(quads)
    remap = np.full(rows * cols, -1, dtype=np.int64)
    remap[used] = np.arange(len(used))

    vr, vc = np.divmod(used, cols)
    x_m = (vc / max(1, cols - 1) - 0.5) * grid.width_m
    y_m = (0.5 - vr / max(1, rows - 1)) * grid.height_m
    return WaterMesh(
        vertices=all_verts[used],
        faces=remap[quads].astype(np.int32),
        uvs=np.column_stack([vc / max(1, cols - 1), 1.0 - vr / max(1, rows - 1)]).astype(np.float32),
        depth_m=(level_m - grid.elevation.ravel()[used]).astype(np.float32),
        position_m=np.column_stack([x_m, y_m, np.full(len(used), level_m)]).astype(np.float32),
    )


def estimate_flow_azimuth(grid: ElevationGrid, flow_mask: np.ndarray, threshold: float = 0.5) -> Optional[float]:
    """Downstream direction of the main channel, in degrees clockwise from north (None if no channel).

    The channel axis is the principal direction of the high-flow cells; it is oriented
    downstream by how bed elevation changes along it. On perfectly flat water (reservoirs
    flattened in the DEM) the orientation sign is arbitrary, which only flips the ripple drift.
    """
    rows, cols = grid.shape
    channel = flow_mask.reshape(grid.shape) > threshold
    if channel.sum() < 10:
        return None
    r, c = np.nonzero(channel)
    x = (c / max(1, cols - 1) - 0.5) * grid.width_m
    y = (0.5 - r / max(1, rows - 1)) * grid.height_m
    pts = np.column_stack([x - x.mean(), y - y.mean()])
    _, _, vt = np.linalg.svd(pts, full_matrices=False)
    axis = vt[0]
    along = pts @ axis
    z = grid.elevation[r, c]
    if np.std(z) > 1e-6 and np.corrcoef(along, z)[0, 1] > 0:
        axis = -axis                     # Point toward lower ground
    return math.degrees(math.atan2(axis[0], axis[1])) % 360.0


def close_water_volume(water: WaterMesh, bottom_z: float) -> WaterMesh:
    """Turn the water surface into a closed, outward-facing volume (surface + skirts + floor).

    Volume shaders (absorption and scattering in Cycles) need a closed mesh. The skirts and
    floor sit under the terrain, so rays only travel through water between the surface and
    the real bed, and the absorbed light follows the true depth.
    """
    n = len(water.vertices)
    top = water.faces.astype(np.int64)
    # Directed edges of the CCW (upward-facing) surface; boundary edges have no reverse twin
    a = top.ravel()
    b = np.roll(top, -1, axis=1).ravel()
    keys = a * n + b
    boundary = ~np.isin(b * n + a, keys)
    ea, eb = a[boundary], b[boundary]

    bottom = water.vertices.copy()
    bottom[:, 2] = bottom_z
    floor = top[:, ::-1] + n                                     # Reversed winding: faces down
    skirts = np.stack([eb, ea, ea + n, eb + n], axis=1)         # Shares each boundary edge reversed
    return WaterMesh(
        vertices=np.concatenate([water.vertices, bottom]).astype(np.float32),
        faces=np.concatenate([top, floor, skirts]).astype(np.int32),
        uvs=np.concatenate([water.uvs, water.uvs]),
        depth_m=np.concatenate([water.depth_m, water.depth_m]),
        position_m=np.concatenate([water.position_m, water.position_m]),
    )
