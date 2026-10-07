"""Constructs 3D mesh geometry, UVs, and topographic vertex weight masks."""

from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Tuple
import numpy as np

from .dem_sources.base import ElevationGrid
from .heightmap import calculate_slope_and_aspect, calculate_topographic_curvature


@dataclass
class TerrainMeshData:
    """Pre-computed geometric buffers ready for Blender mesh creation or export."""
    vertices: np.ndarray            # (N, 3) float32 coordinates [X, Y, Z]
    faces: np.ndarray               # (M, 4) int32 quad indices
    uvs: np.ndarray                 # (N, 2) float32 UV coordinates [U, V]
    vertex_weights: Dict[str, np.ndarray]  # Topographic masks for plant distribution
    width_m: float
    height_m: float
    elevation_min_m: float
    elevation_max_m: float
    vertical_relief_m: float


def build_terrain_mesh_data(
    grid: ElevationGrid,
    vertical_scale: float = 1.0,
    center_origin: bool = True,
    base_elevation_zero: bool = True,
) -> TerrainMeshData:
    """Generate 3D vertex positions, quad faces, UVs, and botanical weight masks from an ElevationGrid.
    
    Args:
        grid: ElevationGrid instance.
        vertical_scale: Vertical exaggeration multiplier (default 1.0 = real scale).
        center_origin: If True, centers the mesh at X=0, Y=0.
        base_elevation_zero: If True, subtracts minimum elevation so base is at Z=0.
    """
    elev = grid.elevation
    rows, cols = elev.shape

    # Real metric cell spacing
    cell_x_m = grid.width_m / max(1, cols - 1)
    cell_y_m = grid.height_m / max(1, rows - 1)

    # 1D coordinate axes
    x_coords = np.linspace(0.0, grid.width_m, cols, dtype=np.float32)
    # y coords: North is top of array (index 0), South is bottom (index rows-1)
    # In Blender 3D space, Y+ is typically North
    y_coords = np.linspace(grid.height_m, 0.0, rows, dtype=np.float32)

    if center_origin:
        x_coords -= (grid.width_m / 2.0)
        y_coords -= (grid.height_m / 2.0)

    # Generate 2D coordinate meshgrid
    xx, yy = np.meshgrid(x_coords, y_coords)

    # Elevation Z
    base_z = grid.min_elevation_m if base_elevation_zero else 0.0
    zz = (elev - base_z) * float(vertical_scale)

    # Flatten into (N, 3) vertex array
    vertices = np.column_stack([xx.ravel(), yy.ravel(), zz.ravel()]).astype(np.float32)

    # UV coordinates (U from 0 to 1 West-to-East, V from 0 to 1 South-to-North)
    u_coords = np.linspace(0.0, 1.0, cols, dtype=np.float32)
    v_coords = np.linspace(1.0, 0.0, rows, dtype=np.float32)
    uu, vv = np.meshgrid(u_coords, v_coords)
    uvs = np.column_stack([uu.ravel(), vv.ravel()]).astype(np.float32)

    # Quad faces
    # For a grid of (rows, cols), faces are quads connecting (r, c), (r+1, c), (r+1, c+1), (r, c+1)
    r_idx = np.arange(rows - 1)[:, np.newaxis]
    c_idx = np.arange(cols - 1)[np.newaxis, :]

    v0 = (r_idx * cols + c_idx).ravel()
    v1 = ((r_idx + 1) * cols + c_idx).ravel()
    v2 = ((r_idx + 1) * cols + (c_idx + 1)).ravel()
    v3 = (r_idx * cols + (c_idx + 1)).ravel()

    faces = np.column_stack([v0, v1, v2, v3]).astype(np.int32)

    # Calculate topographic botanical masks for Procedural Plant Generator
    slope_deg, aspect_rad = calculate_slope_and_aspect(elev, cell_x_m, cell_y_m)
    curvature = calculate_topographic_curvature(elev)

    # Elevation mask: 0 (valley) to 1 (peak)
    elev_range = max(0.001, grid.elevation_range_m)
    elevation_mask = np.clip((elev - grid.min_elevation_m) / elev_range, 0.0, 1.0).ravel()

    # Slope mask: 0 (flat floor) to 1 (cliff >= 60 degrees)
    slope_mask = np.clip(slope_deg / 60.0, 0.0, 1.0).ravel()

    # Flat ground mask: inverted slope (ideal for dense forest / tree clustering)
    flat_ground_mask = (1.0 - slope_mask)

    # Ridge vs valley mask
    curv_min = float(np.percentile(curvature, 2))
    curv_max = float(np.percentile(curvature, 98))
    curv_diff = max(1e-4, curv_max - curv_min)
    valley_mask = np.clip((curvature - curv_min) / curv_diff, 0.0, 1.0).ravel()

    vertex_weights = {
        "RTG_Elevation": elevation_mask.astype(np.float32),
        "RTG_Slope": slope_mask.astype(np.float32),
        "RTG_Plant_Density": (flat_ground_mask * (1.0 - elevation_mask * 0.5)).astype(np.float32),
        "RTG_Valley": valley_mask.astype(np.float32),
    }

    return TerrainMeshData(
        vertices=vertices,
        faces=faces,
        uvs=uvs,
        vertex_weights=vertex_weights,
        width_m=grid.width_m,
        height_m=grid.height_m,
        elevation_min_m=grid.min_elevation_m,
        elevation_max_m=grid.max_elevation_m,
        vertical_relief_m=grid.elevation_range_m,
    )
