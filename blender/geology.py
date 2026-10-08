"""Geology on Blender terrains: rock attributes on the mesh and stored map units."""

from __future__ import annotations
import json

import numpy as np

from ..core.dem_sources.base import ElevationGrid
from ..core.mesh_builder import TerrainAnalysis
from ..core.geology import GeologyMap, MapUnit, RockLayers, assign_rocks, fetch_geology_map

# Mesh attributes read by the RTG material (and later by the rock scatter)
POSITION_ATTRIBUTE = "RTG_Position_m"       # Real-world meters: x east, y north (from center), z altitude
UNIT_ATTRIBUTE = "RTG_Unit_ID"


def srgb_to_linear(c: np.ndarray) -> np.ndarray:
    """sRGB 0..1 to scene-linear (color attributes store linear values)."""
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4).astype(np.float32)


def _set_attribute(mesh, name: str, data_type: str, values: np.ndarray, component: str):
    attr = mesh.attributes.get(name)
    if attr is not None and (attr.data_type != data_type or attr.domain != 'POINT'):
        mesh.attributes.remove(attr)
        attr = None
    if attr is None:
        attr = mesh.attributes.new(name, data_type, 'POINT')
    attr.data.foreach_set(component, np.ascontiguousarray(values).ravel())


def _rgba(rgb: np.ndarray, alpha: float | np.ndarray = 1.0) -> np.ndarray:
    a = np.broadcast_to(np.asarray(alpha, dtype=np.float32), (len(rgb),))
    return np.column_stack([rgb, a]).astype(np.float32)


def write_position_attribute(mesh, grid: ElevationGrid):
    rows, cols = grid.shape
    x = np.linspace(-grid.width_m / 2.0, grid.width_m / 2.0, cols, dtype=np.float32)
    y = np.linspace(grid.height_m / 2.0, -grid.height_m / 2.0, rows, dtype=np.float32)
    xx, yy = np.meshgrid(x, y)
    pos = np.column_stack([xx.ravel(), yy.ravel(), grid.elevation.ravel()]).astype(np.float32)
    _set_attribute(mesh, POSITION_ATTRIBUTE, 'FLOAT_VECTOR', pos, "vector")


def write_rock_attributes(mesh, rocks: RockLayers):
    """Store per-vertex rock data as point attributes (readable by shaders and Geometry Nodes)."""
    _set_attribute(mesh, "RTG_Rock_Color", 'FLOAT_COLOR', _rgba(srgb_to_linear(rocks.color)), "color")
    _set_attribute(mesh, "RTG_Rock_Color_Alt", 'FLOAT_COLOR', _rgba(srgb_to_linear(rocks.color_alt)), "color")
    _set_attribute(mesh, "RTG_Rock_Fracture", 'FLOAT_COLOR', rocks.fracture, "color")
    _set_attribute(mesh, "RTG_Rock_Surface", 'FLOAT_COLOR', rocks.surface, "color")
    _set_attribute(mesh, "RTG_Rock_Joint_m", 'FLOAT', rocks.joint_m, "value")
    _set_attribute(mesh, "RTG_Rock_Bedding_m", 'FLOAT', rocks.bedding_m, "value")
    _set_attribute(mesh, "RTG_Strata_Depth_m", 'FLOAT', rocks.strata_depth_m, "value")
    _set_attribute(mesh, "RTG_Hardness", 'FLOAT', rocks.hardness, "value")
    _set_attribute(mesh, "RTG_Rock_ID", 'INT', rocks.rock_id, "value")
    _set_attribute(mesh, UNIT_ATTRIBUTE, 'INT', rocks.unit_id, "value")
    _set_attribute(mesh, "RTG_Boulder_Density", 'FLOAT', rocks.boulder_density, "value")
    _set_attribute(mesh, "RTG_Clast_Size_m", 'FLOAT', rocks.clast_size_m, "value")
    _set_attribute(mesh, "RTG_Clast_Rounding", 'FLOAT', rocks.clast_rounding, "value")
    mesh.update()


def store_geology_on_object(obj, geology: GeologyMap | None, rocks: RockLayers):
    """Keep map units and the rock legend on the object (for the panel and offline reassignment)."""
    if geology and geology.units:
        counts = np.bincount(geology.unit_index.ravel(), minlength=len(geology.units))
        units = [
            {"label": u.label, "rocks": [[k, w] for k, w in u.rocks], "scale": u.scale,
             "share": float(c) / max(1, geology.unit_index.size)}
            for u, c in zip(geology.units, counts)
        ]
        obj["rtg_geology_units"] = json.dumps(units)
        obj["rtg_geology_attribution"] = geology.attribution
    else:
        obj.pop("rtg_geology_units", None)
        obj.pop("rtg_geology_attribution", None)
    counts = np.bincount(rocks.rock_id, minlength=len(rocks.rock_keys))
    obj["rtg_rock_legend"] = json.dumps([[k, float(c) / max(1, rocks.rock_id.size)] for k, c in zip(rocks.rock_keys, counts)])


def stored_geology(obj, grid: ElevationGrid) -> GeologyMap | None:
    """Rebuild the GeologyMap of a terrain from its stored units and unit attribute (no network)."""
    raw = obj.get("rtg_geology_units")
    attr = obj.data.attributes.get(UNIT_ATTRIBUTE)
    if not raw or attr is None:
        return None
    units = [
        MapUnit(map_id=i, name=u["label"], lith="", descrip="", age="", t_age_ma=0.0, b_age_ma=0.0,
                source_id=0, scale=u.get("scale", ""), rocks=[(k, w) for k, w in u["rocks"]])
        for i, u in enumerate(json.loads(raw))
    ]
    index = np.empty(len(attr.data), dtype=np.int32)
    attr.data.foreach_get("value", index)
    if index.size != grid.elevation.size or index.min() < 0 or index.max() >= len(units):
        return None
    return GeologyMap(unit_index=index.reshape(grid.shape), units=units, references={})


def build_rock_layers(props, grid: ElevationGrid, analysis: TerrainAnalysis, geology: GeologyMap | None) -> RockLayers:
    use_map = props.geology_source == "MACROSTRAT" and geology is not None
    return assign_rocks(grid, analysis, geology=geology if use_map else None, default_rock=props.default_rock)


def fetch_geology(props, grid: ElevationGrid, cache_dir) -> GeologyMap | None:
    """Download map units when the Macrostrat source is selected (None otherwise)."""
    if props.geology_source != "MACROSTRAT":
        return None
    return fetch_geology_map(grid.bbox, grid.shape, cache_dir=cache_dir)
