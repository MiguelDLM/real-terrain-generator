"""Water bodies on real terrain: surface mesh, depth and flow direction.

Instead of a flat plane over the whole terrain, the water surface is built from the
terrain grid itself: only cells that are (nearly) under water are kept, every vertex knows
its depth (water level minus bed elevation), and a margin of cells extends under the shore
so the terrain hides the edge. Depth drives the water material (clear and bed-tinted in
the shallows, darker and opaque in deep water, foam at the shoreline), and the flow
direction orients and advects the ripples.

With a river network (core.river) the surface is no longer flat: near each channel it takes
the river's sloping water surface, and every vertex carries the flow velocity, stream
coordinates and whitewater / foam amounts that animate the water shader. Lakes and rivers
combine: the higher surface wins, so a river can run into a reservoir.

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
from .river import RiverField


SHORE_SINK_M = 0.05     # Shore-margin vertices sit this far under the terrain


@dataclass
class WaterMesh:
    vertices: np.ndarray        # (N, 3) object space
    faces: np.ndarray           # (M, 4) quads
    uvs: np.ndarray             # (N, 2) planar, 0..1 over the terrain
    depth_m: np.ndarray         # (N,) water level minus terrain elevation (negative under the shore margin)
    position_m: np.ndarray      # (N, 3) real meters: x east, y north (from center), z = water surface altitude
    velocity: np.ndarray        # (N, 3) river flow in m/s (x east, y north, 0); 0 on still water
    flow_coords: np.ndarray     # (N, 3) stream coordinates in meters (s along, n across) and river weight 0..1
    whitewater: np.ndarray      # (N,) 0..1 rapids and cascades
    foam: np.ndarray            # (N,) 0..1 foam carried downstream of the rapids


def estimate_water_level(elevation: np.ndarray, margin_m: float = 1.0) -> float:
    """Water level just above the lowest ground (0.5th percentile skips isolated DEM pits)."""
    return float(np.percentile(elevation, 0.5)) + margin_m


def water_face_mask(elevation: np.ndarray, level_m, margin_cells: int = 1) -> np.ndarray:
    """(rows-1, cols-1) mask of grid quads to keep: any corner under water, grown by ``margin_cells``.

    ``level_m`` is one altitude or a per-cell water surface (NaN = no water).
    """
    wet = elevation < level_m
    faces = wet[:-1, :-1] | wet[1:, :-1] | wet[:-1, 1:] | wet[1:, 1:]
    for _ in range(max(0, margin_cells)):
        p = np.pad(faces, 1)
        faces = p[1:-1, 1:-1] | p[:-2, 1:-1] | p[2:, 1:-1] | p[1:-1, :-2] | p[1:-1, 2:]
    return faces


def _fill_from_neighbors(values: np.ndarray, iterations: int = 6) -> np.ndarray:
    """Give NaN cells the mean of their defined neighbors, ring by ring."""
    a = values.copy()
    rows, cols = a.shape
    for _ in range(iterations):
        missing = np.isnan(a)
        if not missing.any():
            break
        p = np.pad(a, 1, constant_values=np.nan)
        total = np.zeros_like(a)
        count = np.zeros_like(a)
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                n = p[1 + dr:1 + dr + rows, 1 + dc:1 + dc + cols]
                ok = ~np.isnan(n)
                total[ok] += n[ok]
                count[ok] += 1
        fill = missing & (count > 0)
        a[fill] = total[fill] / count[fill]
    return a


def build_water_mesh(
    grid: ElevationGrid,
    level_m: float,
    horizontal_scale: float = 1.0,
    vertical_scale: float = 1.0,
    center_origin: bool = True,
    margin_cells: int = 1,
    river: Optional[RiverField] = None,
) -> Optional[WaterMesh]:
    """Water surface over the submerged part of ``grid``, in the same object space as the terrain mesh.

    ``level_m`` is the lake / reservoir level; ``river`` adds sloping river surfaces.
    """
    rows, cols = grid.shape
    elev = grid.elevation.astype(np.float64)
    surface = np.where(elev < level_m, float(level_m), np.nan)
    if river is not None:
        surface = np.fmax(surface, river.surface_m)
    face_mask = water_face_mask(elev, surface, margin_cells)
    if not face_mask.any():
        return None
    # Shore-margin vertices continue the neighboring surface, but just under the ground: the
    # water meets the terrain inside the last cell instead of tilting down in plain sight
    margin = np.isnan(surface)
    surface = _fill_from_neighbors(surface, iterations=margin_cells + 4)
    surface = np.where(np.isnan(surface), float(level_m), surface)
    surface = np.where(margin, np.minimum(surface, elev - SHORE_SINK_M), surface)

    flat = ElevationGrid(
        elevation=surface.astype(np.float32), bbox=grid.bbox,
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
    z_m = surface.ravel()[used]
    depth = z_m - elev.ravel()[used]
    n = len(used)
    velocity = np.zeros((n, 3))
    flow_coords = np.zeros((n, 3))
    whitewater = np.zeros(n)
    foam = np.zeros(n)
    if river is not None:
        # DEMs record the water surface of wide rivers: depth comes from the hydraulics there
        river_depth = river.depth_m.ravel()[used]
        depth = np.where(depth > 0.0, np.maximum(depth, river_depth), depth)
        # A river drowned by a lake slows down (same discharge through a deeper section)
        # and its rapids vanish
        drowned = np.nan_to_num(z_m - river.surface_m.ravel()[used], nan=0.0).clip(min=0.0)
        keep = np.maximum(river_depth, 0.5) / (np.maximum(river_depth, 0.5) + drowned)
        velocity[:, :2] = river.velocity.reshape(-1, 2)[used] * keep[:, None]
        flow_coords[:, 0] = river.stream_s.ravel()[used]
        flow_coords[:, 1] = river.stream_n.ravel()[used]
        flow_coords[:, 2] = river.weight.ravel()[used]
        whitewater = river.whitewater.ravel()[used] * keep ** 2
        foam = river.foam.ravel()[used] * keep
    return WaterMesh(
        vertices=all_verts[used],
        faces=remap[quads].astype(np.int32),
        uvs=np.column_stack([vc / max(1, cols - 1), 1.0 - vr / max(1, rows - 1)]).astype(np.float32),
        depth_m=depth.astype(np.float32),
        position_m=np.column_stack([x_m, y_m, z_m]).astype(np.float32),
        velocity=velocity.astype(np.float32),
        flow_coords=flow_coords.astype(np.float32),
        whitewater=whitewater.astype(np.float32),
        foam=foam.astype(np.float32),
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
        velocity=np.concatenate([water.velocity, water.velocity]),
        flow_coords=np.concatenate([water.flow_coords, water.flow_coords]),
        whitewater=np.concatenate([water.whitewater, water.whitewater]),
        foam=np.concatenate([water.foam, water.foam]),
    )
