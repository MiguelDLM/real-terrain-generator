"""Heightmap generation, 16-bit normalization, and topographic analysis."""

from __future__ import annotations
from pathlib import Path
from typing import Tuple
import numpy as np

from .png_io import encode_png_gray16


def normalize_to_uint16(elevation_m: np.ndarray) -> Tuple[np.ndarray, float, float]:
    """Normalize float elevation in meters to a 16-bit grayscale array (0 to 65535).
    
    Returns:
        (array_uint16, min_elevation_m, max_elevation_m)
    """
    min_val = float(np.min(elevation_m))
    max_val = float(np.max(elevation_m))
    diff = max_val - min_val

    if diff < 1e-4:
        # Completely flat terrain
        norm = np.zeros_like(elevation_m, dtype=np.uint16)
        return norm, min_val, max_val

    normalized_01 = (elevation_m - min_val) / diff
    array_uint16 = (normalized_01 * 65535.0).astype(np.uint16)
    return array_uint16, min_val, max_val


def save_heightmap_png16(elevation_m: np.ndarray, output_path: Path) -> Tuple[Path, float, float]:
    """Save an elevation grid as a 16-bit Grayscale PNG file.
    
    Returns:
        (saved_path, min_elevation_m, max_elevation_m)
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    arr_u16, min_elev, max_elev = normalize_to_uint16(elevation_m)
    png_bytes = encode_png_gray16(arr_u16)
    output_path.write_bytes(png_bytes)
    return output_path, min_elev, max_elev


def calculate_slope_and_aspect(
    elevation_m: np.ndarray,
    cell_size_x_m: float,
    cell_size_y_m: float,
) -> Tuple[np.ndarray, np.ndarray]:
    """Calculate slope (in degrees) and aspect (in radians 0..2pi) for each cell in the elevation grid.
    
    Uses central differences for interior cells and single-sided differences for borders.
    """
    dz_dy, dz_dx = np.gradient(elevation_m, cell_size_y_m, cell_size_x_m)

    # Slope in radians: atan(hypot(dz/dx, dz/dy))
    slope_rad = np.arctan(np.hypot(dz_dx, dz_dy))
    slope_deg = np.degrees(slope_rad)

    # Aspect in radians: counterclockwise from East (or atan2(-dz/dy, dz/dx))
    aspect_rad = np.arctan2(-dz_dy, dz_dx) % (2.0 * np.pi)

    return slope_deg, aspect_rad


def calculate_topographic_curvature(elevation_m: np.ndarray) -> np.ndarray:
    """Calculate topographic curvature (Laplacian) to detect ridges and valleys.
    
    Positive values indicate concave features (valleys/creeks),
    negative values indicate convex features (peaks/ridges).
    """
    laplacian = (
        np.roll(elevation_m, 1, axis=0)
        + np.roll(elevation_m, -1, axis=0)
        + np.roll(elevation_m, 1, axis=1)
        + np.roll(elevation_m, -1, axis=1)
        - 4.0 * elevation_m
    )
    # Zero out edges
    laplacian[0, :] = 0
    laplacian[-1, :] = 0
    laplacian[:, 0] = 0
    laplacian[:, -1] = 0
    return laplacian
