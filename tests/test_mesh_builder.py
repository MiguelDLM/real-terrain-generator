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

    # Check all 9 Botanical and Geomorphological masks
    expected_masks = [
        "RTG_Elevation",
        "RTG_Slope",
        "RTG_Plant_Density",
        "RTG_Valley",
        "RTG_Water_Flow",
        "RTG_Wetness",
        "RTG_Talus",
        "RTG_Snow",
        "RTG_Roughness",
    ]
    for m in expected_masks:
        assert m in mesh_data.vertex_weights, f"Missing mask {m}"
        w = mesh_data.vertex_weights[m]
        assert len(w) == 64
        assert np.all((w >= 0.0) & (w <= 1.0)), f"Mask {m} values out of bounds [0, 1]"


def test_build_terrain_mesh_data_scaling():
    rows, cols = 4, 4
    elev = np.ones((rows, cols), dtype=np.float32) * 500.0
    elev[0, 0] = 1500.0  # 1000m relief

    bbox = BoundingBox(min_lat=28.0, min_lon=-16.5, max_lat=28.1, max_lon=-16.4)
    grid = ElevationGrid(
        elevation=elev,
        bbox=bbox,
        width_m=10000.0,
        height_m=10000.0,
        min_elevation_m=500.0,
        max_elevation_m=1500.0,
        provider_name="TestProvider",
        attribution="Test",
        metadata={},
    )

    # Scale 1:1000 (0.001): 10,000m -> 10m
    mesh_data = build_terrain_mesh_data(
        grid,
        vertical_scale=1.0,
        horizontal_scale=0.001,
        center_origin=True,
    )

    xs = mesh_data.vertices[:, 0]
    ys = mesh_data.vertices[:, 1]
    zs = mesh_data.vertices[:, 2]

    # Bounds in X and Y should be [-5.0, 5.0]
    assert np.isclose(np.min(xs), -5.0)
    assert np.isclose(np.max(xs), 5.0)
    assert np.isclose(np.min(ys), -5.0)
    assert np.isclose(np.max(ys), 5.0)

    # Relief was 1000m -> scaled by 0.001 should be 1.0m
    assert np.isclose(np.min(zs), 0.0)
    assert np.isclose(np.max(zs), 1.0)
    assert np.isclose(mesh_data.width_m, 10.0)
    assert np.isclose(mesh_data.height_m, 10.0)


def test_build_terrain_mesh_data_vegetation_controls():
    rows, cols = 6, 6
    # Gradient from low to high elevation
    elev = np.linspace(0.0, 2000.0, rows * cols, dtype=np.float32).reshape((rows, cols))

    bbox = BoundingBox(min_lat=28.0, min_lon=-16.5, max_lat=28.1, max_lon=-16.4)
    grid = ElevationGrid(
        elevation=elev,
        bbox=bbox,
        width_m=2000.0,
        height_m=2000.0,
        min_elevation_m=0.0,
        max_elevation_m=2000.0,
        provider_name="TestProvider",
        attribution="Test",
        metadata={},
    )

    # Restrictive treeline (0.5 = 1000m max altitude for vegetation)
    mesh_data = build_terrain_mesh_data(
        grid,
        max_plant_slope_deg=25.0,
        plant_treeline_ratio=0.5,
        valley_boost=0.0,
    )

    density = mesh_data.vertex_weights["RTG_Plant_Density"]
    elev_mask = mesh_data.vertex_weights["RTG_Elevation"]

    # At highest peaks (elev_mask > 0.6), plant density should be 0.0
    high_mask = elev_mask > 0.6
    assert np.all(density[high_mask] == 0.0)


def test_compute_scaled_vertices_direct():
    from core.mesh_builder import compute_scaled_vertices

    rows, cols = 5, 5
    elev = np.zeros((rows, cols), dtype=np.float32)
    elev[2, 2] = 500.0  # 500m peak at center

    bbox = BoundingBox(min_lat=28.0, min_lon=-16.5, max_lat=28.1, max_lon=-16.4)
    grid = ElevationGrid(
        elevation=elev,
        bbox=bbox,
        width_m=5000.0,
        height_m=5000.0,
        min_elevation_m=0.0,
        max_elevation_m=500.0,
        provider_name="TestProvider",
        attribution="Test",
        metadata={},
    )

    # Scale 0.01 (1:100): 5000m -> 50m, 500m -> 5m
    verts = compute_scaled_vertices(grid, vertical_scale=2.0, horizontal_scale=0.01, center_origin=True)
    assert verts.shape == (25, 3)

    # Center vertex (index 12) should be at X=0, Y=0, Z = 500 * 2.0 * 0.01 = 10.0
    center_v = verts[12]
    assert np.isclose(center_v[0], 0.0)
    assert np.isclose(center_v[1], 0.0)
    assert np.isclose(center_v[2], 10.0)


def test_compute_botanical_weights_direct():
    from core.mesh_builder import compute_botanical_weights

    rows, cols = 8, 8
    elev = np.zeros((rows, cols), dtype=np.float32)

    bbox = BoundingBox(min_lat=28.0, min_lon=-16.5, max_lat=28.1, max_lon=-16.4)
    grid = ElevationGrid(
        elevation=elev,
        bbox=bbox,
        width_m=1000.0,
        height_m=1000.0,
        min_elevation_m=0.0,
        max_elevation_m=0.0,
        provider_name="TestProvider",
        attribution="Test",
        metadata={},
    )

    weights = compute_botanical_weights(grid, max_plant_slope_deg=30.0, plant_treeline_ratio=0.8, valley_boost=0.5)
    for m in [
        "RTG_Plant_Density", "RTG_Slope", "RTG_Elevation", "RTG_Valley",
        "RTG_Water_Flow", "RTG_Wetness", "RTG_Talus", "RTG_Snow", "RTG_Roughness",
    ]:
        assert m in weights
    # On completely flat ground, slope is 0 so plant density is 1.0
    assert np.allclose(weights["RTG_Slope"], 0.0)
    assert np.all(weights["RTG_Plant_Density"] > 0.9)


def test_geomorphological_algorithms():
    from core.mesh_builder import (
        compute_flow_accumulation,
        compute_topographic_wetness_index,
        compute_talus_scree_mask,
        compute_structural_snow_mask,
        compute_terrain_ruggedness_index,
    )

    # 16x16 synthetic valley: V-shaped depression draining from left to right
    rows, cols = 16, 16
    y, x = np.mgrid[0:rows, 0:cols]
    elev = (np.abs(y - 8.0) * 50.0 + (cols - x) * 10.0).astype(np.float32)

    # 1. Flow Accumulation
    flow = compute_flow_accumulation(elev)
    assert flow.shape == (rows, cols)
    # The lowest valley axis (y=8) should accumulate water drainage
    assert np.max(flow[8, :]) > np.max(flow[0, :])

    # 2. TWI
    slope_deg = np.zeros_like(elev)
    slope_deg[0, :] = 45.0  # steep ridges
    slope_deg[8, :] = 2.0   # flat valley bottom
    twi = compute_topographic_wetness_index(flow, slope_deg, cell_x_m=30.0)
    assert twi.shape == (rows, cols)
    assert np.all((twi >= 0.0) & (twi <= 1.0))
    # Valley floor should have higher wetness than steep ridge
    assert twi[8, cols // 2] > twi[0, cols // 2]

    # 3. Talus / Scree
    talus = compute_talus_scree_mask(slope_deg)
    assert talus.shape == (rows, cols)
    assert np.all((talus >= 0.0) & (talus <= 1.0))

    # 4. Snow Mask
    curv = np.zeros_like(elev)
    snow = compute_structural_snow_mask(elev, slope_deg, curv, snow_line_ratio=0.5)
    assert snow.shape == (rows, cols)
    assert np.all((snow >= 0.0) & (snow <= 1.0))

    # 5. TRI (Ruggedness)
    tri = compute_terrain_ruggedness_index(elev)
    assert tri.shape == (rows, cols)
    assert np.all((tri >= 0.0) & (tri <= 1.0))





def _grid(elev, width_m=3000.0, lat=46.0):
    return ElevationGrid(
        elevation=elev.astype(np.float32),
        bbox=BoundingBox(min_lat=lat - 0.01, min_lon=7.0, max_lat=lat + 0.01, max_lon=7.02),
        width_m=width_m,
        height_m=width_m,
        min_elevation_m=float(elev.min()),
        max_elevation_m=float(elev.max()),
        provider_name="TestProvider",
        attribution="Test",
        metadata={},
    )


def test_absolute_treeline_and_snowline():
    from core.mesh_builder import compute_botanical_weights
    # Gentle ramp from 1000 m to 3000 m altitude
    elev = np.tile(np.linspace(1000.0, 3000.0, 40), (40, 1))
    weights = compute_botanical_weights(_grid(elev, width_m=40000.0), treeline_m=2000.0, snowline_m=2500.0)
    density = weights["RTG_Plant_Density"].reshape(elev.shape)
    snow = weights["RTG_Snow"].reshape(elev.shape)
    assert np.all(density[:, elev[0] > 2000.0] == 0.0)
    assert np.all(density[:, elev[0] < 1600.0] > 0.5)
    assert np.all(snow[:, elev[0] < 2400.0] == 0.0)
    assert np.all(snow[:, elev[0] > 2900.0] > 0.5)

    # A low, flat region never gets snow with a real snow line, whatever its relative relief
    low = np.tile(np.linspace(0.0, 20.0, 40), (40, 1))
    assert np.all(compute_botanical_weights(_grid(low), snowline_m=2500.0)["RTG_Snow"] == 0.0)


def test_pole_facing_slopes_get_shade():
    from core.mesh_builder import analyze_terrain
    rows = np.arange(40, dtype=np.float32)[:, None]
    # Row 0 is north: elevation rising southwards means the slope faces north
    north_facing = np.repeat(rows * 30.0, 40, axis=1)
    assert analyze_terrain(_grid(north_facing, lat=46.0)).shade.mean() > 0.5
    assert analyze_terrain(_grid(north_facing, lat=-46.0)).shade.mean() == 0.0
    assert analyze_terrain(_grid(north_facing[::-1], lat=46.0)).shade.mean() == 0.0


def test_dynamic_masks_match_full_computation():
    from core.mesh_builder import analyze_terrain, compute_dynamic_masks, compute_botanical_weights
    y, x = np.mgrid[-1:1:32j, -1:1:32j]
    grid = _grid(1500.0 + 800.0 * np.exp(-(x**2 + y**2) * 3))
    full = compute_botanical_weights(grid, max_plant_slope_deg=30.0, snow_line_ratio=0.5)
    dynamic = compute_dynamic_masks(grid, analyze_terrain(grid), max_plant_slope_deg=30.0, snow_line_ratio=0.5)
    for name, values in dynamic.items():
        assert np.array_equal(values, full[name])


def test_build_without_masks():
    elev = np.random.default_rng(1).random((8, 8)) * 100.0
    mesh_data = build_terrain_mesh_data(_grid(elev), compute_masks=False)
    assert mesh_data.vertex_weights == {}
    assert mesh_data.vertices.shape == (64, 3)
