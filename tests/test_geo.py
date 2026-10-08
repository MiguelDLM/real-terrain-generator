"""Unit tests for geographic coordinate transforms and tile calculations."""

import math
import pytest
from core.geo import (
    lat_lon_to_tile,
    tile_to_lat_lon,
    tile_bounds,
    bbox_from_center,
    get_tiles_for_bbox,
    meters_per_pixel,
    recommend_zoom_for_area,
    BoundingBox,
)


def test_lat_lon_to_tile_and_back():
    lat = 28.2723   # Mount Teide, Tenerife
    lon = -16.6425
    zoom = 12

    x, y = lat_lon_to_tile(lat, lon, zoom)
    assert isinstance(x, int)
    assert isinstance(y, int)
    assert 0 <= x < (2 ** zoom)
    assert 0 <= y < (2 ** zoom)

    # Reverse: corner coordinates should enclose the original coordinate
    bounds = tile_bounds(x, y, zoom)
    assert bounds.min_lat <= lat <= bounds.max_lat
    assert bounds.min_lon <= lon <= bounds.max_lon


def test_bbox_from_center():
    center_lat = 0.0  # Equator
    center_lon = 0.0
    width_km = 10.0
    height_km = 10.0

    bbox = bbox_from_center(center_lat, center_lon, width_km, height_km)
    assert isinstance(bbox, BoundingBox)
    assert bbox.min_lat < bbox.max_lat
    assert bbox.min_lon < bbox.max_lon
    assert pytest.approx(bbox.center[0], abs=1e-5) == 0.0
    assert pytest.approx(bbox.center[1], abs=1e-5) == 0.0


def test_get_tiles_for_bbox():
    bbox = BoundingBox(min_lat=28.2, min_lon=-16.7, max_lat=28.3, max_lon=-16.6)
    zoom = 12

    tiles, (min_x, max_x, min_y, max_y) = get_tiles_for_bbox(bbox, zoom)
    assert len(tiles) >= 1
    assert max_x >= min_x
    assert max_y >= min_y
    assert len(tiles) == (max_x - min_x + 1) * (max_y - min_y + 1)


def test_meters_per_pixel():
    m_px_eq = meters_per_pixel(0.0, 12)
    m_px_pole = meters_per_pixel(60.0, 12)
    # Cosine at 60 deg is 0.5, so ground resolution is halved (finer meters per pixel)
    assert pytest.approx(m_px_pole, rel=1e-2) == m_px_eq * 0.5


def test_recommend_zoom():
    assert recommend_zoom_for_area(1.0, 1.0) == 14
    assert recommend_zoom_for_area(5.0, 5.0) == 13
    assert recommend_zoom_for_area(10.0, 10.0) == 12
    assert recommend_zoom_for_area(50.0, 50.0) == 10


def test_recommend_zoom_matches_mesh_resolution():
    from core.geo import meters_per_pixel
    zoom = recommend_zoom_for_area(10.0, 10.0, target_resolution=512, lat_deg=28.0)
    # Data at the chosen zoom covers 10 km with >= 512 samples, one zoom lower does not
    assert 10000.0 / meters_per_pixel(28.0, zoom) >= 512
    assert 10000.0 / meters_per_pixel(28.0, zoom - 1) < 512


def test_count_tiles_and_climate_lines():
    from core.geo import count_tiles_for_area, estimate_climate_lines
    assert count_tiles_for_area(28.27, -16.64, 10.0, 10.0, 12) >= 1
    tree_eq, snow_eq = estimate_climate_lines(0.0)
    tree_alps, _ = estimate_climate_lines(46.0)
    assert snow_eq > tree_eq > tree_alps > 1500.0
    assert estimate_climate_lines(-46.0) == estimate_climate_lines(46.0)
