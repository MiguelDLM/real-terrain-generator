"""Unit tests for the river network, its hydraulics and the river water surface."""

import numpy as np
import pytest
from core.geo import BoundingBox
from core.dem_sources.base import ElevationGrid
from core.river import RiverParams, priority_flood, analyze_river, channel_hydraulics
from core.water import build_water_mesh


def _grid(elev, width_m=2000.0):
    rows, cols = elev.shape
    return ElevationGrid(
        elevation=elev.astype(np.float32),
        bbox=BoundingBox(min_lat=16.8, min_lon=-93.1, max_lat=16.82, max_lon=-93.08),
        width_m=width_m, height_m=width_m * (rows - 1) / max(1, cols - 1),
        min_elevation_m=float(elev.min()), max_elevation_m=float(elev.max()),
        provider_name="Test", attribution="Test", metadata={},
    )


def _valley(rows=41, cols=41, drop_per_row=1.0, wall=20.0):
    """V-shaped valley along column 20 whose floor descends southwards (row 0 = north)."""
    c = np.arange(cols)[None, :]
    r = np.arange(rows)[:, None]
    return 500.0 + np.abs(c - 20) * wall + (rows - r) * drop_per_row


def test_priority_flood_fills_pits_and_drains_to_the_edge():
    elev = np.full((9, 9), 10.0)
    elev[4, 4] = 2.0                          # Pit in a plateau
    elev[0, 4] = 5.0                          # Outlet on the northern edge
    filled = priority_flood(elev, epsilon=1e-3)
    assert filled[4, 4] > 10.0 - 1e-6         # Pit raised to the spill level
    assert np.all(filled >= elev)
    # Every interior cell has a strictly lower neighbor: no flats, no pits
    for r in range(1, 8):
        for c in range(1, 8):
            assert filled[r - 1:r + 2, c - 1:c + 2].min() < filled[r, c]


def test_main_stem_follows_the_valley_and_flows_south():
    grid = _grid(_valley())
    river = analyze_river(grid, RiverParams(discharge_m3s=20.0, min_area_km2=0.05))
    floor = river.channel[:, 20]
    assert floor[2:-2].all()                  # The valley floor is a channel
    assert river.channel.sum() < river.channel.size * 0.25
    # Velocity points south (negative y) on the valley floor
    v = river.velocity[10:30, 20]
    assert np.all(v[:, 1] < 0.0)
    assert np.all(np.abs(v[:, 0]) < np.abs(v[:, 1]))
    # Stream coordinate s grows downstream
    s = river.stream_s[5:35, 20]
    assert np.all(np.diff(s) > 0.0)


def test_through_flow_carries_the_full_discharge_on_the_main_stem():
    grid = _grid(_valley())
    river = analyze_river(grid, RiverParams(discharge_m3s=50.0, min_area_km2=0.05, through_flow=True))
    assert river.discharge[3, 20] == pytest.approx(50.0)
    river = analyze_river(grid, RiverParams(discharge_m3s=50.0, min_area_km2=0.05, through_flow=False))
    assert river.discharge[3, 20] < 25.0
    assert river.discharge[-2, 20] == pytest.approx(50.0, rel=0.05)


def test_steep_torrent_is_supercritical_and_lowland_river_is_calm():
    depth, velocity, froude = channel_hydraulics(
        np.array([5.0, 700.0]), np.array([0.06, 0.0003]), np.array([6.7, 79.0]), manning_n=0.04)
    assert froude[0] > 1.0                    # Mountain torrent: rapids
    assert froude[1] < 0.5                    # Large lowland river: tranquil
    assert depth[1] > depth[0] and velocity[0] > 1.0


def test_whitewater_appears_on_steep_reaches_and_foam_drifts_downstream():
    elev = _valley(drop_per_row=0.3)
    elev[10:16] -= np.arange(6)[:, None] * 6.0        # Steep step (cascade) in rows 10-15
    elev[16:] -= 36.0
    grid = _grid(elev)
    river = analyze_river(grid, RiverParams(discharge_m3s=10.0, min_area_km2=0.05))
    white = river.whitewater[:, 20]
    assert white[11:15].max() > 0.5
    assert white[30:38].max() < white[11:15].max()
    foam = river.foam[:, 20]
    # Foam persists past the cascade, fading with distance
    assert foam[20] > white[20]
    assert foam[20] > foam[35]


def test_river_water_surface_descends_with_the_valley_and_stays_narrow():
    grid = _grid(_valley(drop_per_row=2.0))
    river = analyze_river(grid, RiverParams(discharge_m3s=5.0, min_area_km2=0.05))
    mesh = build_water_mesh(grid, level_m=-1000.0, horizontal_scale=1.0, river=river)
    assert mesh is not None
    north = mesh.position_m[mesh.position_m[:, 1] > 600.0, 2]
    south = mesh.position_m[mesh.position_m[:, 1] < -600.0, 2]
    assert north.mean() > south.mean() + 50.0
    xs = mesh.position_m[mesh.depth_m > 0.0, 0]
    assert np.abs(xs).max() < 200.0            # Water stays near the valley floor
    assert mesh.velocity.shape == (len(mesh.vertices), 3)
    assert mesh.flow_coords.shape == (len(mesh.vertices), 3)
    assert mesh.flow_coords[:, 2].max() == pytest.approx(1.0)


def test_lake_only_water_has_no_river_flow():
    grid = _grid(_valley(drop_per_row=0.0))
    mesh = build_water_mesh(grid, level_m=505.0, horizontal_scale=1.0)
    assert mesh is not None
    assert np.allclose(mesh.velocity, 0.0)
    assert np.allclose(mesh.flow_coords[:, 2], 0.0)
    assert np.allclose(mesh.position_m[:, 2], 505.0)


def test_through_flow_crosses_a_flat_reservoir_from_edge_to_outlet():
    # L-shaped canyon: enters flat (reservoir) from the west edge at row 5, turns south at column 30
    rows, cols = 41, 41
    elev = np.full((rows, cols), 800.0)
    elev[4:7, 0:31] = 600.0                    # Flat reservoir arm along the west-east canyon
    elev[4:41, 29:32] = 600.0                  # Canyon turning south
    elev[30:, 29:32] -= np.arange(11)[:, None] * 5.0   # Below the dam it descends to the south edge
    grid = _grid(elev)
    river = analyze_river(grid, RiverParams(discharge_m3s=100.0, min_area_km2=5.0, through_flow=True))
    main = river.discharge >= 100.0 - 1e-6
    assert main[4:7, 0].any()                  # Starts at the western edge
    assert main[-1, 29:32].any()               # Leaves at the southern edge
    assert main[4:7, 10:25].any(axis=0).all()  # Runs along the flat arm


def test_river_drowned_by_a_lake_slows_down_and_loses_its_rapids():
    elev = _valley(drop_per_row=0.3)
    elev[10:16] -= np.arange(6)[:, None] * 6.0
    elev[16:] -= 36.0
    grid = _grid(elev)
    river = analyze_river(grid, RiverParams(discharge_m3s=10.0, min_area_km2=0.05))
    free = build_water_mesh(grid, level_m=-1000.0, river=river)
    drowned = build_water_mesh(grid, level_m=elev.max() - 100.0, river=river)
    assert free.whitewater.max() > 0.5
    assert drowned.whitewater.max() < 0.05
    assert np.linalg.norm(drowned.velocity, axis=1).max() < 0.5 * np.linalg.norm(free.velocity, axis=1).max()


def test_river_surface_is_level_across_and_never_rises_downstream():
    rng = np.random.default_rng(0)
    elev = _valley(drop_per_row=0.5, wall=4.0) + rng.normal(0.0, 3.0, (41, 41))   # Noisy DEM floor
    grid = _grid(elev)
    river = analyze_river(grid, RiverParams(discharge_m3s=200.0, min_area_km2=5.0))
    s = river.surface_m
    for r in range(5, 36):
        row = s[r][~np.isnan(s[r])]
        assert row.max() - row.min() < 0.5          # Level from bank to bank
    stem = river.discharge >= 200.0 - 1e-6
    profile = [s[r][stem[r]].mean() for r in range(41) if stem[r].any()]
    assert np.all(np.diff(profile) <= 1e-6)          # North to south: never uphill
