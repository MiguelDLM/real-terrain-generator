"""Unit tests for heightmap normalization, slope, curvature and export."""

import numpy as np
import pytest
from core.heightmap import (
    normalize_to_uint16,
    calculate_slope_and_aspect,
    calculate_topographic_curvature,
)


def test_normalize_to_uint16():
    # Grid with range 100m to 500m
    elev = np.array([
        [100.0, 200.0],
        [300.0, 500.0]
    ], dtype=np.float32)

    norm, min_val, max_val = normalize_to_uint16(elev)
    assert min_val == 100.0
    assert max_val == 500.0
    assert norm.dtype == np.uint16
    assert norm[0, 0] == 0          # min maps to 0
    assert norm[1, 1] == 65535      # max maps to 65535
    assert pytest.approx(norm[0, 1], abs=20) == int(65535 * (100.0 / 400.0))


def test_calculate_slope_flat():
    # Completely flat grid: slope must be 0
    flat = np.ones((10, 10), dtype=np.float32) * 50.0
    slope, aspect = calculate_slope_and_aspect(flat, 10.0, 10.0)
    assert np.allclose(slope, 0.0)


def test_calculate_slope_45_degrees():
    # 45-degree ramp along X axis: dx = 10m, dz = 10m per cell
    ramp = np.outer(np.ones(10), np.arange(10) * 10.0).astype(np.float32)
    slope, aspect = calculate_slope_and_aspect(ramp, 10.0, 10.0)
    # Interior cells should be close to 45 degrees
    interior_slope = slope[1:-1, 1:-1]
    assert np.allclose(interior_slope, 45.0, atol=1e-1)


def test_curvature():
    # Ridge / peak feature in the middle
    grid = np.zeros((7, 7), dtype=np.float32)
    grid[3, 3] = 100.0
    curv = calculate_topographic_curvature(grid)
    # Center should be convex (negative curvature)
    assert curv[3, 3] < 0.0
