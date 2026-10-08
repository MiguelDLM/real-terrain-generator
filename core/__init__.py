"""Real Terrain Generator - Core Engine."""

from .geo import (
    BoundingBox,
    lat_lon_to_tile,
    tile_to_lat_lon,
    tile_bounds,
    meters_per_pixel,
    bbox_from_center,
    get_tiles_for_bbox,
    recommend_zoom_for_area,
    count_tiles_for_area,
    estimate_climate_lines,
)
from .png_io import decode_png_rgb, encode_png_gray16
from .geocoding import search_location, GeocodingResult
from .heightmap import (
    normalize_to_uint16,
    save_heightmap_png16,
    calculate_slope_and_aspect,
    calculate_topographic_curvature,
)
from .mesh_builder import (
    TerrainMeshData,
    build_terrain_mesh_data,
    compute_scaled_vertices,
    compute_botanical_weights,
    TerrainAnalysis,
    analyze_terrain,
    compute_dynamic_masks,
)
from .dem_sources import (
    DEMProvider,
    ElevationGrid,
    AWSTerrariumProvider,
    OpenTopographyProvider,
)

__all__ = [
    "BoundingBox",
    "lat_lon_to_tile",
    "tile_to_lat_lon",
    "tile_bounds",
    "meters_per_pixel",
    "bbox_from_center",
    "get_tiles_for_bbox",
    "recommend_zoom_for_area",
    "count_tiles_for_area",
    "estimate_climate_lines",
    "decode_png_rgb",
    "encode_png_gray16",
    "search_location",
    "GeocodingResult",
    "normalize_to_uint16",
    "save_heightmap_png16",
    "calculate_slope_and_aspect",
    "calculate_topographic_curvature",
    "TerrainMeshData",
    "build_terrain_mesh_data",
    "compute_scaled_vertices",
    "compute_botanical_weights",
    "TerrainAnalysis",
    "analyze_terrain",
    "compute_dynamic_masks",
    "DEMProvider",
    "ElevationGrid",
    "AWSTerrariumProvider",
    "OpenTopographyProvider",
]
