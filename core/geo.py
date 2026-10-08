"""Geographic and cartographic coordinate transformation utilities.

Handles WGS84 coordinates (latitude/longitude), Slippy Map Web Mercator tiles
(x, y, zoom), bounding boxes, ground resolution (meters/pixel), and metric dimensions.
"""

from __future__ import annotations
import math
from typing import NamedTuple, Tuple, List

EARTH_RADIUS_KM = 6378.137
EARTH_CIRCUMFERENCE_M = 40075016.686


class BoundingBox(NamedTuple):
    """Geographic bounding box in WGS84 decimal degrees."""
    min_lat: float
    min_lon: float
    max_lat: float
    max_lon: float

    @property
    def width_deg(self) -> float:
        return self.max_lon - self.min_lon

    @property
    def height_deg(self) -> float:
        return self.max_lat - self.min_lat

    @property
    def center(self) -> Tuple[float, float]:
        return ((self.min_lat + self.max_lat) / 2.0, (self.min_lon + self.max_lon) / 2.0)


def clamp(val: float, min_val: float, max_val: float) -> float:
    return max(min_val, min(val, max_val))


def lat_lon_to_tile(lat_deg: float, lon_deg: float, zoom: int) -> Tuple[int, int]:
    """Convert WGS84 latitude and longitude into Slippy Map tile (x, y) coordinates at a zoom level."""
    lat_deg = clamp(lat_deg, -85.05112878, 85.05112878)
    lon_deg = clamp(lon_deg, -180.0, 180.0)
    
    n = 2.0 ** zoom
    x = int(math.floor((lon_deg + 180.0) / 360.0 * n))
    lat_rad = math.radians(lat_deg)
    y = int(math.floor((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n))
    
    max_tile = int(n) - 1
    return clamp(x, 0, max_tile), clamp(y, 0, max_tile)


def tile_to_lat_lon(xtile: int, ytile: int, zoom: int) -> Tuple[float, float]:
    """Convert Slippy Map tile (x, y) coordinate to Northwest corner latitude and longitude in degrees."""
    n = 2.0 ** zoom
    lon_deg = xtile / n * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1.0 - 2.0 * ytile / n)))
    lat_deg = math.degrees(lat_rad)
    return lat_deg, lon_deg


def tile_bounds(xtile: int, ytile: int, zoom: int) -> BoundingBox:
    """Return the bounding box (min_lat, min_lon, max_lat, max_lon) for a specific tile."""
    north, west = tile_to_lat_lon(xtile, ytile, zoom)
    south, east = tile_to_lat_lon(xtile + 1, ytile + 1, zoom)
    return BoundingBox(min_lat=south, min_lon=west, max_lat=north, max_lon=east)


def meters_per_pixel(lat_deg: float, zoom: int, tile_size: int = 256) -> float:
    """Calculate ground resolution in meters per pixel at a given latitude and zoom level."""
    lat_rad = math.radians(clamp(lat_deg, -85.05, 85.05))
    return (EARTH_CIRCUMFERENCE_M * math.cos(lat_rad)) / ((2.0 ** zoom) * tile_size)


def bbox_from_center(center_lat: float, center_lon: float, width_km: float, height_km: float) -> BoundingBox:
    """Create a BoundingBox given a center coordinate and metric width/height in kilometers."""
    delta_lat_rad = (height_km / 2.0) / EARTH_RADIUS_KM
    delta_lat_deg = math.degrees(delta_lat_rad)

    lat_rad = math.radians(center_lat)
    cos_lat = max(0.01, math.cos(lat_rad))
    delta_lon_rad = (width_km / 2.0) / (EARTH_RADIUS_KM * cos_lat)
    delta_lon_deg = math.degrees(delta_lon_rad)

    min_lat = clamp(center_lat - delta_lat_deg, -85.0, 85.0)
    max_lat = clamp(center_lat + delta_lat_deg, -85.0, 85.0)
    min_lon = clamp(center_lon - delta_lon_deg, -180.0, 180.0)
    max_lon = clamp(center_lon + delta_lon_deg, -180.0, 180.0)

    return BoundingBox(min_lat=min_lat, min_lon=min_lon, max_lat=max_lat, max_lon=max_lon)


def get_tiles_for_bbox(bbox: BoundingBox, zoom: int) -> Tuple[List[Tuple[int, int, int]], Tuple[int, int, int, int]]:
    """Determine all (zoom, x, y) tiles covering a bounding box.
    
    Returns:
        (tiles_list, (min_x, max_x, min_y, max_y))
    """
    x1, y1 = lat_lon_to_tile(bbox.max_lat, bbox.min_lon, zoom)  # Top-left (NW)
    x2, y2 = lat_lon_to_tile(bbox.min_lat, bbox.max_lon, zoom)  # Bottom-right (SE)

    min_x, max_x = min(x1, x2), max(x1, x2)
    min_y, max_y = min(y1, y2), max(y1, y2)

    tiles = []
    for y in range(min_y, max_y + 1):
        for x in range(min_x, max_x + 1):
            tiles.append((zoom, x, y))

    return tiles, (min_x, max_x, min_y, max_y)


def haversine_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the great-circle distance between two points on Earth in meters."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = math.sin(delta_phi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return EARTH_RADIUS_KM * 1000.0 * c


def recommend_zoom_for_area(
    width_km: float,
    height_km: float,
    target_resolution: int | None = None,
    lat_deg: float = 0.0,
    max_zoom: int = 15,
) -> int:
    """Recommend a tile zoom level for the desired area.

    Without ``target_resolution`` a balanced default based on area size is returned.
    With it, the lowest zoom whose source pixels cover the longest side with at least
    ``target_resolution`` samples is chosen, so the mesh never upsamples (blurs) the DEM.
    """
    max_dim = max(width_km, height_km)
    if target_resolution:
        for zoom in range(6, max_zoom + 1):
            source_px = (max_dim * 1000.0) / meters_per_pixel(lat_deg, zoom)
            if source_px >= target_resolution:
                return zoom
        return max_zoom

    if max_dim <= 2.0:
        return 14  # High detail (~9.5m/px)
    elif max_dim <= 6.0:
        return 13  # Medium-high detail (~19m/px)
    elif max_dim <= 15.0:
        return 12  # Mountain-scale detail (~38m/px)
    elif max_dim <= 40.0:
        return 11  # Regional (~76m/px)
    elif max_dim <= 100.0:
        return 10  # Broad range (~150m/px)
    else:
        return 9   # Macro landscape


def count_tiles_for_area(center_lat: float, center_lon: float, width_km: float, height_km: float, zoom: int) -> int:
    """Number of Slippy Map tiles needed to cover an area (used to warn about large downloads)."""
    bbox = bbox_from_center(center_lat, center_lon, width_km, height_km)
    _, (min_x, max_x, min_y, max_y) = get_tiles_for_bbox(bbox, zoom)
    return (max_x - min_x + 1) * (max_y - min_y + 1)


def estimate_climate_lines(lat_deg: float) -> Tuple[float, float]:
    """Rough tree line and snow line altitudes (meters) for a latitude.

    Coarse global trend (Körner & Paulsen 2004): the tree line stays near 3500 m
    between the tropics and ~30°, then drops towards sea level near 70°. The
    seasonal snow line is taken ~800 m above it. Continentality and local climate
    shift these by hundreds of meters, so they are only starting values.
    """
    a = abs(lat_deg)
    if a <= 30.0:
        treeline = 3500.0
    else:
        treeline = max(0.0, 3500.0 - (a - 30.0) * 87.5)
    return treeline, treeline + 800.0


def solar_position(lat_deg: float, day_of_year: int, solar_hour: float) -> Tuple[float, float]:
    """Sun elevation and compass azimuth (degrees, 0 = north, 90 = east) at local solar time.

    Simple declination model (Cooper 1969): accurate to about a degree, enough to light
    a scene. ``solar_hour`` is apparent solar time, so 12.0 is always solar noon.
    """
    phi = math.radians(lat_deg)
    decl = math.radians(23.44) * math.sin(2.0 * math.pi * (284 + day_of_year) / 365.0)
    hour_angle = math.radians(15.0 * (solar_hour - 12.0))
    sin_el = math.sin(phi) * math.sin(decl) + math.cos(phi) * math.cos(decl) * math.cos(hour_angle)
    elevation = math.degrees(math.asin(max(-1.0, min(1.0, sin_el))))
    azimuth = math.degrees(math.atan2(
        math.sin(hour_angle),
        math.cos(hour_angle) * math.sin(phi) - math.tan(decl) * math.cos(phi),
    )) + 180.0
    return elevation, azimuth % 360.0


def haze_extinction_per_m(visibility_km: float) -> float:
    """Extinction coefficient (1/m) of air with this meteorological visibility.

    Koschmieder's law: contrast falls to 2% at the visibility distance, so
    extinction = ln(50) / visibility ≈ 3.912 / visibility.
    """
    return math.log(50.0) / (max(visibility_km, 0.01) * 1000.0)
