"""Geologic map units from the Macrostrat API, rasterized onto a terrain grid.

Macrostrat (Peters, Husson & Czaplewski 2018, doi:10.1029/2018GC007467) harmonizes
geologic maps from many surveys (USGS, IGME, BGS, GSC...) at four scales:
``large`` (~1:24k-1:100k, local surveys), ``medium`` (~1:250k-1:500k), ``small``
(~1:1M-1:5M) and ``tiny`` (global, ~1:15M+). Coverage and detail vary by country; the
finest scale available at each point is used.

The API answers point queries with the containing polygon (GeoJSON). An area is mapped
adaptively: query a point, rasterize the returned polygon, then query only where the
grid is still uncovered. Cells no query reached take the nearest mapped unit.

Data license: CC-BY 4.0 (Macrostrat and the original map sources it cites).
"""

from __future__ import annotations
import hashlib
import json
import urllib.parse
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple
import numpy as np

from ..geo import BoundingBox
from .lithology import classify_lithology

MACROSTRAT_API = "https://macrostrat.org/api/v2"
MAP_SCALES = ("large", "medium", "small", "tiny")
DEFAULT_USER_AGENT = "RealTerrainGenerator/0.1.0 (Blender Extension; geology)"


@dataclass
class MapUnit:
    """A geologic map polygon's attributes."""
    map_id: int
    name: str
    lith: str
    descrip: str
    age: str
    t_age_ma: float
    b_age_ma: float
    source_id: int
    scale: str
    lith_names: List[str] = field(default_factory=list)
    rocks: List[Tuple[str, float]] = field(default_factory=list)  # (rock profile key, share)

    @property
    def label(self) -> str:
        text = self.name or self.descrip or self.lith or f"Unit {self.map_id}"
        return f"{text} ({self.age})" if self.age else text


@dataclass
class GeologyMap:
    """Per-cell geologic unit index over a terrain grid (row 0 = north)."""
    unit_index: np.ndarray            # (rows, cols) int32 into ``units``
    units: List[MapUnit]
    references: Dict[int, str]        # source_id -> citation
    queries: int = 0

    @property
    def attribution(self) -> str:
        return "Macrostrat (CC-BY 4.0): " + "; ".join(sorted(set(self.references.values())))


class MacrostratClient:
    """Small HTTP client with an on-disk JSON cache (so revisiting an area costs no requests)."""

    def __init__(self, cache_dir: Optional[Path] = None, user_agent: str = DEFAULT_USER_AGENT, timeout: float = 20.0):
        self.cache_dir = Path(cache_dir) / "macrostrat" if cache_dir else None
        self.user_agent = user_agent
        self.timeout = timeout
        self._lith_defs: Optional[Dict[int, dict]] = None

    def get_json(self, path: str, params: Dict[str, str]) -> dict:
        url = f"{MACROSTRAT_API}/{path}?{urllib.parse.urlencode(params)}"
        cache_file = None
        if self.cache_dir:
            cache_file = self.cache_dir / (hashlib.sha1(url.encode()).hexdigest() + ".json")
            if cache_file.exists():
                return json.loads(cache_file.read_text(encoding="utf-8"))

        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Macrostrat request failed: HTTP {e.code} {e.reason}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"Network error contacting Macrostrat: {e.reason}")

        if cache_file:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(data), encoding="utf-8")
        return data

    def lithology_definitions(self) -> Dict[int, dict]:
        """Macrostrat lithology vocabulary: lith_id -> {name, type, class, ...}."""
        if self._lith_defs is None:
            data = self.get_json("defs/lithologies", {"all": ""})
            self._lith_defs = {d["lith_id"]: d for d in data["success"]["data"]}
        return self._lith_defs

    def query_point(self, lat: float, lon: float, scales: Sequence[str] = MAP_SCALES) -> Tuple[str, List[dict], Dict[int, str]]:
        """Map polygons at a point from the finest scale that has data: (scale, features, refs)."""
        for scale in scales:
            data = self.get_json("geologic_units/map", {
                "lat": f"{lat:.5f}", "lng": f"{lon:.5f}", "scale": scale, "format": "geojson",
            })["success"]
            features = data["data"].get("features", []) if isinstance(data["data"], dict) else []
            if features:
                refs = {int(k): v for k, v in (data.get("refs") or {}).items()}
                return scale, features, refs
        return "", [], {}

    def unit_from_feature(self, feature: dict, scale: str) -> MapUnit:
        p = feature["properties"]
        defs = self.lithology_definitions()
        lith_names = [defs[i]["name"] for i in p.get("liths") or [] if i in defs]
        unit = MapUnit(
            map_id=int(p.get("map_id", 0)),
            name=p.get("name") or p.get("strat_name") or "",
            lith=p.get("lith") or "",
            descrip=p.get("descrip") or "",
            age=p.get("best_int_name") or "",
            t_age_ma=float(p.get("t_age") or 0.0),
            b_age_ma=float(p.get("b_age") or 0.0),
            source_id=int(p.get("source_id") or 0),
            scale=scale,
            lith_names=lith_names,
        )
        # Free text names specific rocks more often than the controlled vocabulary does
        unit.rocks = classify_lithology(unit.descrip, unit.name, unit.lith) or classify_lithology(" ".join(lith_names))
        return unit


def polygon_mask(geometry: dict, lons: np.ndarray, lats: np.ndarray) -> np.ndarray:
    """Boolean (rows, cols) mask of grid cell centers inside a GeoJSON (Multi)Polygon (even-odd rule)."""
    rings: List[np.ndarray] = []
    if geometry["type"] == "Polygon":
        rings = [np.asarray(r, dtype=np.float64) for r in geometry["coordinates"]]
    elif geometry["type"] == "MultiPolygon":
        rings = [np.asarray(r, dtype=np.float64) for poly in geometry["coordinates"] for r in poly]

    inside = np.zeros((len(lats), len(lons)), dtype=bool)
    for ring in rings:
        if len(ring) < 3:
            continue
        # Only test the cells within the ring's bounding box
        c0, c1 = np.searchsorted(lons, [ring[:, 0].min(), ring[:, 0].max()])
        r_desc = lats[::-1]
        a, b = np.searchsorted(r_desc, [ring[:, 1].min(), ring[:, 1].max()])
        r0, r1 = len(lats) - b, len(lats) - a
        if c1 <= c0 or r1 <= r0:
            continue
        px = lons[c0:c1][None, :]
        py = lats[r0:r1][:, None]
        hit = np.zeros((r1 - r0, c1 - c0), dtype=bool)
        x1, y1 = ring[:-1, 0], ring[:-1, 1]
        x2, y2 = ring[1:, 0], ring[1:, 1]
        for i in range(len(x1)):
            if y1[i] == y2[i]:
                continue
            crosses = (y1[i] > py) != (y2[i] > py)
            x_cross = (x2[i] - x1[i]) * (py - y1[i]) / (y2[i] - y1[i]) + x1[i]
            hit ^= crosses & (px < x_cross)
        inside[r0:r1, c0:c1] ^= hit
    return inside


def fill_nearest(index: np.ndarray, missing: int = -1) -> np.ndarray:
    """Give unmapped cells the value of the nearest mapped cell (iterative 4-neighbour growth)."""
    out = index.copy()
    if np.all(out == missing):
        return out
    while np.any(out == missing):
        grown = out.copy()
        for axis, shift in ((0, 1), (0, -1), (1, 1), (1, -1)):
            neighbour = np.roll(out, shift, axis=axis)
            if axis == 0:
                neighbour[0 if shift == 1 else -1, :] = missing
            else:
                neighbour[:, 0 if shift == 1 else -1] = missing
            take = (grown == missing) & (neighbour != missing)
            grown[take] = neighbour[take]
        out = grown
    return out


def _sample_points(n: int) -> List[Tuple[float, float]]:
    """n x n lattice of (u, v) in [0, 1], center first, then outward."""
    pts = [((i + 0.5) / n, (j + 0.5) / n) for i in range(n) for j in range(n)]
    return sorted(pts, key=lambda p: (p[0] - 0.5) ** 2 + (p[1] - 0.5) ** 2)


def fetch_geology_map(
    bbox: BoundingBox,
    shape: Tuple[int, int],
    client: Optional[MacrostratClient] = None,
    cache_dir: Optional[Path] = None,
    lattice: int = 6,
    max_queries: int = 30,
    progress: Optional[Callable[[int, int], None]] = None,
) -> GeologyMap:
    """Map geologic units over ``bbox`` on a ``shape`` = (rows, cols) grid."""
    client = client or MacrostratClient(cache_dir=cache_dir)
    rows, cols = shape
    lons = np.linspace(bbox.min_lon, bbox.max_lon, cols)
    lats = np.linspace(bbox.max_lat, bbox.min_lat, rows)
    index = np.full(shape, -1, dtype=np.int32)

    units: List[MapUnit] = []
    unit_slot: Dict[int, int] = {}
    references: Dict[int, str] = {}
    scales: Sequence[str] = MAP_SCALES
    queries = 0

    for u, v in _sample_points(lattice):
        if queries >= max_queries:
            break
        r = min(rows - 1, int(v * rows))
        c = min(cols - 1, int(u * cols))
        if index[r, c] >= 0:
            continue
        scale, features, refs = client.query_point(float(lats[r]), float(lons[c]), scales)
        queries += 1
        if progress:
            progress(queries, max_queries)
        if not features:
            continue
        # Keep later queries at this scale or coarser, so units stay mutually consistent
        scales = MAP_SCALES[MAP_SCALES.index(scale):]
        references.update(refs)
        for feature in features:
            map_id = int(feature["properties"].get("map_id", 0))
            if map_id not in unit_slot:
                unit_slot[map_id] = len(units)
                units.append(client.unit_from_feature(feature, scale))
            mask = polygon_mask(feature["geometry"], lons, lats) & (index < 0)
            index[mask] = unit_slot[map_id]
        if index[r, c] < 0:
            # Point lies on a boundary the rasterization missed; trust the query
            index[r, c] = unit_slot[int(features[0]["properties"].get("map_id", 0))]

    if units:
        index = fill_nearest(index)
    return GeologyMap(unit_index=index, units=units, references=references, queries=queries)
