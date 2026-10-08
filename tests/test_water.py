"""Unit tests for water surface construction, level and flow direction."""

import numpy as np
import pytest
from core.geo import BoundingBox
from core.dem_sources.base import ElevationGrid
from core.mesh_builder import analyze_terrain
from core.water import build_water_mesh, estimate_water_level, water_face_mask, estimate_flow_azimuth


def _grid(elev, width_m=1000.0):
    return ElevationGrid(
        elevation=elev.astype(np.float32),
        bbox=BoundingBox(min_lat=16.8, min_lon=-93.1, max_lat=16.81, max_lon=-93.09),
        width_m=width_m, height_m=width_m,
        min_elevation_m=float(elev.min()), max_elevation_m=float(elev.max()),
        provider_name="Test", attribution="Test", metadata={},
    )


def _canyon(rows=40, cols=40, drop_per_row=0.0):
    """North-south canyon: walls rise east and west of a flat river at column 20."""
    c = np.arange(cols)[None, :]
    r = np.arange(rows)[:, None]
    return 200.0 + np.abs(c - 20) * 25.0 + (rows - r) * drop_per_row


def test_water_mesh_covers_only_the_river():
    grid = _grid(_canyon())
    level = estimate_water_level(grid.elevation)
    assert level == pytest.approx(201.0)
    mesh = build_water_mesh(grid, level, horizontal_scale=0.01, vertical_scale=1.0)
    assert mesh is not None
    # River column plus a one-cell margin on each side (faces between columns 18..21)
    cols_used = np.unique(np.round((mesh.position_m[:, 0] / 1000.0 + 0.5) * 39).astype(int))
    assert cols_used.min() >= 18 and cols_used.max() <= 22
    assert mesh.depth_m.max() == pytest.approx(1.0) and mesh.depth_m.min() < 0.0
    assert np.allclose(mesh.vertices[:, 2], (level - 200.0) * 0.01)
    assert mesh.faces.max() < len(mesh.vertices)


def test_water_face_mask_margin_and_dry_terrain():
    elev = _canyon()
    assert water_face_mask(elev, 201.0, 2).sum() > water_face_mask(elev, 201.0, 0).sum()
    assert build_water_mesh(_grid(elev), 100.0) is None


def test_flow_azimuth_points_downstream():
    # River bed descends southwards (row 0 = north is highest)
    grid = _grid(_canyon(drop_per_row=2.0))
    flow = analyze_terrain(grid).static_masks["RTG_Water_Flow"]
    azimuth = estimate_flow_azimuth(grid, flow)
    assert azimuth is not None
    assert abs(((azimuth - 180.0) + 180.0) % 360.0 - 180.0) < 15.0
    assert estimate_flow_azimuth(grid, np.zeros(grid.elevation.size)) is None


def test_closed_water_volume_is_watertight_and_consistently_oriented():
    from core.water import close_water_volume
    grid = _grid(_canyon())
    surface = build_water_mesh(grid, 201.0, horizontal_scale=0.01)
    closed = close_water_volume(surface, bottom_z=-1.0)
    faces = closed.faces.astype(np.int64)
    a = faces.ravel()
    b = np.roll(faces, -1, axis=1).ravel()
    n = len(closed.vertices)
    edges = a * n + b
    # Every directed edge appears once and its reverse appears once: closed and outward-consistent
    assert len(np.unique(edges)) == len(edges)
    assert np.all(np.isin(b * n + a, edges))
    assert len(closed.vertices) == 2 * len(surface.vertices)
    assert np.all(closed.vertices[len(surface.vertices):, 2] == -1.0)
