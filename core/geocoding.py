"""Geocoding service using OpenStreetMap Nominatim.

Allows users to type landmark or place names (e.g., "Mount Everest", "Teide", "Grand Canyon")
and automatically obtain decimal coordinates (lat, lon) and geographic bounding boxes.
"""

from __future__ import annotations
import json
import urllib.parse
import urllib.request
import urllib.error
from dataclasses import dataclass
from typing import List, Optional
from .geo import BoundingBox

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
DEFAULT_USER_AGENT = "RealTerrainGenerator/0.1.0 (Blender Extension; migueldlm1307@gmail.com)"


@dataclass
class GeocodingResult:
    """Result of a place name search query."""
    display_name: str
    latitude: float
    longitude: float
    bbox: BoundingBox
    place_type: str
    importance: float


def search_location(query: str, limit: int = 5, user_agent: str = DEFAULT_USER_AGENT) -> List[GeocodingResult]:
    """Search for locations by name using OpenStreetMap Nominatim."""
    query = query.strip()
    if not query:
        return []

    params = {
        "q": query,
        "format": "json",
        "limit": str(limit),
        "addressdetails": "1",
    }
    url = f"{NOMINATIM_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        raise RuntimeError(f"Geocoding request failed: {e.reason}")
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Failed to parse geocoding response: {e}")

    results: List[GeocodingResult] = []
    for item in data:
        try:
            lat = float(item["lat"])
            lon = float(item["lon"])
            # Nominatim returns boundingbox as [south, north, west, east]
            raw_bb = item.get("boundingbox", [lat - 0.05, lat + 0.05, lon - 0.05, lon + 0.05])
            south = float(raw_bb[0])
            north = float(raw_bb[1])
            west = float(raw_bb[2])
            east = float(raw_bb[3])

            results.append(
                GeocodingResult(
                    display_name=item.get("display_name", ""),
                    latitude=lat,
                    longitude=lon,
                    bbox=BoundingBox(min_lat=south, min_lon=west, max_lat=north, max_lon=east),
                    place_type=item.get("type", "feature"),
                    importance=float(item.get("importance", 0.0)),
                )
            )
        except (KeyError, ValueError):
            continue

    return results
