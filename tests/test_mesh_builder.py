"""Unit tests for 3D terrain mesh generation and botanical weight masks."""

import numpy as np
import pytest
from core.geo import BoundingBox
from core.dem_sources.base import ElevationGrid
from core.mesh_builder import build_terrain_mesh_data


def test_build_terrain_mesh_data():
    rows, cols = 8, 8
    # Synthetic elevation pyramid
    y, x = np.mgrid[-1:1:8j, -1:1:8j]
    elev = (1000.0 - (x**2 + y**2) * 500.0).astype(np.float32)

    bbox = BoundingBox(min_lat=28.0, min_lon=-16.5, max_lat=28.1, max_lon=-16.4)
    grid = ElevationGrid(
        elevation=elev,
        bbox=bbox,
        width_m=10000.0,
        height_m=10000.0,
        min_elevation_m=float(np.min(elev)),
        max_elevation_m=float(np.max(elev)),
        provider_name="TestProvider",
        attribution="Test",
        metadata={},
    )

    mesh_data = build_terrain_mesh_data(grid, vertical_scale=1.0, center_origin=True)

    # Check vertex count: rows * cols = 64
    assert len(mesh_data.vertices) == rows * cols
    assert mesh_data.vertices.shape == (64, 3)

    # Check face count: (rows - 1) * (cols - 1) = 49 quads
    assert len(mesh_data.faces) == (rows - 1) * (cols - 1)
    assert mesh_data.faces.shape == (49, 4)

    # Check UVs
    assert len(mesh_data.uvs) == 64
    assert np.all(mesh_data.uvs >= 0.0) and np.all(mesh_data.uvs <= 1.0)

    # Check Botanical masks for Procedural Plant Generator
    assert "RTG_Elevation" in mesh_data.vertex_weights
    assert "RTG_Slope" in mesh_data.vertex_weights
    assert "RTG_Plant_Density" in mesh_data.vertex_weights
    assert "RTG_Valley" in mesh_data.vertex_weights

    elev_w = mesh_data.vertex_weights["RTG_Elevation"]
    assert len(elev_w) == 64
    assert 0.0 <= np.min(elev_w) <= np.max(elev_w) <= 1.0

    slope_w = mesh_data.vertex_weights["RTG_Slope"]
    assert len(slope_w) == 64
    assert np.all((slope_w >= 0.0) & (slope_w <= 1.0))
