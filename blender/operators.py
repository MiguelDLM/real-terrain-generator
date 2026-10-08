"""Blender operators for Real Terrain Generator."""

from __future__ import annotations
import json
import time
from contextlib import contextmanager
from pathlib import Path

try:
    import bpy
    from bpy.app.handlers import persistent
    from bpy_extras.io_utils import ExportHelper
    BLENDER_AVAILABLE = True
except ImportError:
    BLENDER_AVAILABLE = False

import numpy as np

from ..core.geo import (
    BoundingBox,
    bbox_from_center,
    recommend_zoom_for_area,
    haversine_distance_m,
    estimate_climate_lines,
    solar_position,
)
from ..core.dem_sources.base import ElevationGrid
from ..core.geocoding import search_location
from ..core.dem_sources.aws_terrarium import AWSTerrariumProvider
from ..core.dem_sources.opentopography import OpenTopographyProvider
from ..core.heightmap import save_heightmap_png16
from ..core.mesh_builder import (
    TerrainAnalysis,
    analyze_terrain,
    build_terrain_mesh_data,
    compute_scaled_vertices,
    compute_dynamic_masks,
)
from .preferences import get_opentopography_key, get_cache_directory
from .geology import (
    srgb_to_linear,
    build_rock_layers, fetch_geology, stored_geology, store_geology_on_object,
    write_position_attribute, write_rock_attributes,
)
from .materials import apply_terrain_material, update_material_scale
from .terrain_detail import apply_terrain_detail, update_detail_scale
from .materials import set_material_water_level
from .water import ensure_water_object, write_water_mesh, set_flow_direction, find_water, set_volume_mode
from .rocks import ensure_rocks, find_rocks, update_rocks
from .scene_setup import (
    ensure_sky, update_sky, hide_sun_lamps, ensure_atmosphere, remove_atmosphere, find_atmosphere,
    set_haze_visibility, place_camera, apply_render_preset,
)
from ..core.water import build_water_mesh, close_water_volume, estimate_water_level, estimate_flow_azimuth
from ..core.river import RiverParams, analyze_river
from ..core.geology import plan_surface_terms
from .properties import TERRAIN_SETTINGS

# Raw elevation in meters, stored on the mesh so the terrain stays editable after reopening the .blend
ELEVATION_ATTRIBUTE = "RTG_Elevation_m"
# Masks packed into color attributes so shaders can read them (vertex groups are not visible to shaders)
COLOR_PACKS = {
    "RTG_Geo_Masks": ("RTG_Water_Flow", "RTG_Snow", "RTG_Talus", "RTG_Wetness"),
    "RTG_Eco_Masks": ("RTG_Plant_Density", "RTG_Slope", "RTG_Valley", "RTG_Roughness"),
}

MASK_DESCRIPTIONS = {
    "RTG_Plant_Density": "Vegetation density: gentle slopes below the tree line, boosted in valleys",
    "RTG_Water_Flow": "Drainage network from D8 flow accumulation (streams and gullies)",
    "RTG_Wetness": "Topographic Wetness Index: soil moisture for moss, meadows and bogs",
    "RTG_Talus": "Scree aprons at the angle of repose (30-38°) below cliffs",
    "RTG_Snow": "Snowpack above the snow line; sloughs off cliffs, lingers on shaded slopes and cirques",
    "RTG_Roughness": "Terrain Ruggedness Index: rocky, broken ground",
    "RTG_Slope": "Slope angle from 0° (flat) to 90° (vertical)",
    "RTG_Elevation": "Altitude from the lowest point (0) to the summit (1)",
    "RTG_Valley": "Concave hollows and valley floors (Laplacian curvature)",
}

# Session cache: mesh session_uid -> (ElevationGrid, TerrainAnalysis | None)
_GRID_CACHE: dict[int, tuple[ElevationGrid, TerrainAnalysis | None]] = {}
_SYNCING = False
_LAST_ACTIVE_TERRAIN = ""

# Heatmap of the inspected mask, shown in Solid mode (no Weight Paint mode needed)
MASK_VIEW_ATTRIBUTE = "RTG_Mask_View"
# Weight-paint-like ramp: 0 blue, 0.25 cyan, 0.5 green, 0.75 yellow, 1 red (sRGB)
_HEAT_STOPS = np.array([0.0, 0.25, 0.5, 0.75, 1.0])
_HEAT_RGB = np.array([[0, 0, 1], [0, 1, 1], [0, 1, 0], [1, 1, 0], [1, 0, 0]], dtype=np.float32)

# Last river analysis: (grid, params, RiverField); live sliders and rescaling reuse it
_RIVER_CACHE: list = []

# Debounced live updates: slider drags only queue work, done once the drag pauses
LIVE_DEBOUNCE_S = 0.2
_PENDING_UPDATES: dict[str, str] = {}   # update kind -> object name
_LAST_REQUEST = 0.0


def get_active_terrain(context):
    """The active object if it is an RTG terrain, otherwise the first selected RTG terrain."""
    if not BLENDER_AVAILABLE or not context:
        return None
    active = getattr(context, "active_object", None)
    if active and active.get("rtg_is_terrain"):
        return active
    for obj in getattr(context, "selected_objects", None) or []:
        if obj.get("rtg_is_terrain"):
            return obj
    return None


def get_grid_for_object(obj) -> ElevationGrid | None:
    """Rebuild the ElevationGrid of a terrain from the session cache or its stored elevation attribute."""
    if not obj or not obj.get("rtg_is_terrain") or obj.type != 'MESH':
        return None
    mesh = obj.data
    cached = _GRID_CACHE.get(mesh.session_uid)
    if cached:
        return cached[0]

    rows, cols = int(obj.get("rtg_grid_rows", 0)), int(obj.get("rtg_grid_cols", 0))
    attr = mesh.attributes.get(ELEVATION_ATTRIBUTE)
    if not attr or rows * cols == 0 or len(attr.data) != rows * cols:
        return None

    elevation = np.empty(rows * cols, dtype=np.float32)
    attr.data.foreach_get("value", elevation)
    elevation = elevation.reshape((rows, cols))
    bbox = obj.get("rtg_bbox", (0.0, 0.0, 0.0, 0.0))
    grid = ElevationGrid(
        elevation=elevation,
        bbox=BoundingBox(*bbox),
        width_m=float(obj["rtg_width_m"]),
        height_m=float(obj["rtg_height_m"]),
        min_elevation_m=float(elevation.min()),
        max_elevation_m=float(elevation.max()),
        provider_name=str(obj.get("rtg_provider", "")),
        attribution=str(obj.get("rtg_attribution", "")),
        metadata={},
    )
    _GRID_CACHE[mesh.session_uid] = (grid, None)
    return grid


def get_analysis_for_object(obj, grid: ElevationGrid) -> TerrainAnalysis:
    """Parameter-independent masks of a terrain, computed once per session."""
    uid = obj.data.session_uid
    _, analysis = _GRID_CACHE.get(uid, (grid, None))
    if analysis is None:
        analysis = analyze_terrain(grid)
        _GRID_CACHE[uid] = (grid, analysis)
    return analysis


@contextmanager
def ensure_editable_mode(obj):
    """Leave Edit / Sculpt mode while writing mesh data (both keep their own copy of it).

    Weight Paint mode is kept, so masks can be inspected while sliders change them.
    """
    orig_mode = getattr(obj, "mode", 'OBJECT')
    switched = orig_mode in {'EDIT', 'SCULPT'} and bpy.context.view_layer.objects.active == obj
    if switched:
        bpy.ops.object.mode_set(mode='OBJECT')
    try:
        yield
    finally:
        if switched:
            bpy.ops.object.mode_set(mode=orig_mode)


def tag_redraw_all_viewports():
    """Force all 3D Viewport spaces to refresh."""
    wm = getattr(bpy.context, "window_manager", None)
    if not wm:
        return
    for window in wm.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def compute_scale_factors(props, scene, width_m: float, height_m: float) -> tuple[float, float]:
    """Return (mesh_scale, object_scale): Blender units per real meter baked into vertices / put on the object."""
    unit = scene.unit_settings.scale_length if scene.unit_settings.scale_length > 0 else 1.0
    meter_bu = 1.0 / unit
    if props.scale_mode == "FIT":
        # target_size_m is a LENGTH property, so it is already stored in Blender units
        bu_per_m = props.target_size_m / max(1.0, width_m, height_m)
    elif props.scale_mode == "CUSTOM":
        bu_per_m = meter_bu / props.scale_ratio
    else:  # REAL 1:1
        bu_per_m = meter_bu
    if props.apply_mesh_scale:
        return bu_per_m, 1.0
    return meter_bu, bu_per_m / meter_bu


# Version of the data contract other add-ons can rely on (see README, "Integration")
CONTRACT_VERSION = 1


def sync_scale_dependents(obj, grid: ElevationGrid, props, mesh_scale: float, obj_scale: float):
    """Propagate a scale change to everything authored in meters: material, wall detail, water, contract."""
    obj["rtg_bu_per_m"] = mesh_scale * obj_scale          # Scene units per real meter (world space)
    obj["rtg_contract_version"] = CONTRACT_VERSION
    update_material_scale(obj, mesh_scale * obj_scale)
    update_detail_scale(obj, mesh_scale)
    water = find_water(obj)
    if water is not None:
        rebuild_water(water, grid, props, mesh_scale, obj_scale)
    rocks = find_rocks(obj)
    if rocks is not None:
        update_rocks(rocks, mesh_scale)


def river_for_grid(grid: ElevationGrid, props):
    """River network and hydraulics for the River water mode (None for lakes only)."""
    if props.water_mode != "RIVER":
        return None
    params = RiverParams(
        discharge_m3s=props.river_discharge_m3s, min_area_km2=props.river_min_area_km2,
        manning_n=props.river_roughness, through_flow=props.river_through_flow,
    )
    if not _RIVER_CACHE or _RIVER_CACHE[0] is not grid or _RIVER_CACHE[1] != params:
        _RIVER_CACHE[:] = [grid, params, analyze_river(grid, params)]
    return _RIVER_CACHE[2]


def rebuild_water(water, grid: ElevationGrid, props, mesh_scale: float, obj_scale: float) -> bool:
    """Rebuild the water mesh for its stored level, the water mode and the current terrain scale."""
    level_m = float(water.get("rtg_water_level_m", estimate_water_level(grid.elevation)))
    data = build_water_mesh(grid, level_m, mesh_scale, props.vertical_scale, props.center_mesh,
                            river=river_for_grid(grid, props))
    if data is None:
        return False
    if props.water_volume:
        # Floor 5 m below the lowest ground, hidden under the terrain
        data = close_water_volume(data, -5.0 * props.vertical_scale * mesh_scale)
    write_water_mesh(water, data, mesh_scale * obj_scale)
    if water.active_material:
        set_volume_mode(water.active_material, props.water_volume)
    return True


def update_active_water_quick(context):
    """Switch the selected terrain's water between shader and volume depth modes."""
    if _SYNCING:
        return
    props = context.scene.rtg_properties
    terrain = get_active_terrain(context)
    grid = get_grid_for_object(terrain)
    water = find_water(terrain) if terrain else None
    if grid and water:
        mesh_scale, obj_scale = compute_scale_factors(props, context.scene, grid.width_m, grid.height_m)
        rebuild_water(water, grid, props, mesh_scale, obj_scale)
        store_settings_on_object(terrain, props)


def store_settings_on_object(obj, props):
    obj["rtg_settings"] = {name: getattr(props, name) for name in TERRAIN_SETTINGS}


def load_settings_from_object(obj, props):
    """Copy the settings a terrain was made with back into the sidebar (no live update)."""
    global _SYNCING
    settings = obj.get("rtg_settings")
    if not settings:
        return
    _SYNCING = True
    try:
        for name, value in settings.items():
            if name in TERRAIN_SETTINGS:
                try:
                    setattr(props, name, value)
                except (TypeError, ValueError, AttributeError):
                    pass
    finally:
        _SYNCING = False
_LAST_ACTIVE_TERRAIN = ""

# Heatmap of the inspected mask, shown in Solid mode (no Weight Paint mode needed)
MASK_VIEW_ATTRIBUTE = "RTG_Mask_View"
# Weight-paint-like ramp: 0 blue, 0.25 cyan, 0.5 green, 0.75 yellow, 1 red (sRGB)
_HEAT_STOPS = np.array([0.0, 0.25, 0.5, 0.75, 1.0])
_HEAT_RGB = np.array([[0, 0, 1], [0, 1, 1], [0, 1, 0], [1, 1, 0], [1, 0, 0]], dtype=np.float32)

# Last river analysis: (grid, params, RiverField); live sliders and rescaling reuse it
_RIVER_CACHE: list = []

# Debounced live updates: slider drags only queue work, done once the drag pauses
LIVE_DEBOUNCE_S = 0.2
_PENDING_UPDATES: dict[str, str] = {}   # update kind -> object name
_LAST_REQUEST = 0.0


def create_grid_mesh(name: str, vertices: np.ndarray, faces: np.ndarray, uvs: np.ndarray):
    """Build a mesh from NumPy buffers with foreach_set (C speed, no Python per-element loops)."""
    mesh = bpy.data.meshes.new(name)
    n_faces = len(faces)
    mesh.vertices.add(len(vertices))
    mesh.vertices.foreach_set("co", vertices.ravel())
    mesh.loops.add(n_faces * 4)
    mesh.loops.foreach_set("vertex_index", faces.ravel())
    mesh.polygons.add(n_faces)
    mesh.polygons.foreach_set("loop_start", np.arange(0, n_faces * 4, 4, dtype=np.int32))
    mesh.update(calc_edges=True)
    mesh.shade_smooth()
    uv_layer = mesh.uv_layers.new(name="UVMap")
    uv_layer.data.foreach_set("uv", uvs[faces.ravel()].ravel())
    return mesh


def _write_vertex_group(obj, name: str, weights: np.ndarray):
    """Write one mask to a vertex group, leaving zero-weight vertices out of it.

    Weights are quantized to 100 levels and written one batch per level, using a
    single sort instead of scanning the whole array for every level.
    """
    vg = obj.vertex_groups.get(name) or obj.vertex_groups.new(name=name)
    quantized = (np.clip(weights, 0.0, 1.0) * 100).astype(np.int16)
    order = np.argsort(quantized, kind="stable")
    bounds = np.searchsorted(quantized[order], np.arange(102))
    if bounds[1] > 0:
        vg.remove(order[:bounds[1]].tolist())
    for level in range(1, 101):
        start, end = bounds[level], bounds[level + 1]
        if end > start:
            vg.add(order[start:end].tolist(), level / 100.0, 'REPLACE')


def apply_masks_to_object(obj, weights_dict: dict[str, np.ndarray], as_vertex_groups: bool = False):
    """Store masks as float point attributes (fast, light) or as vertex groups (particle systems, painting).

    Geometry Nodes reads either by name. Switching mode removes the other representation,
    since a vertex group and an attribute cannot share a name.
    """
    with ensure_editable_mode(obj):
        mesh = obj.data
        for name, weights in weights_dict.items():
            attr = mesh.attributes.get(name)
            if as_vertex_groups:
                if attr is not None:
                    mesh.attributes.remove(attr)
                _write_vertex_group(obj, name, weights)
                continue
            vg = obj.vertex_groups.get(name)
            if vg is not None:
                obj.vertex_groups.remove(vg)
            if attr is not None and (attr.data_type != 'FLOAT' or attr.domain != 'POINT'):
                mesh.attributes.remove(attr)
                attr = None
            if attr is None:
                attr = mesh.attributes.new(name, 'FLOAT', 'POINT')
            attr.data.foreach_set("value", np.ascontiguousarray(weights, dtype=np.float32))
        mesh.update()


def read_mask(obj, name: str) -> np.ndarray | None:
    attr = obj.data.attributes.get(name)
    if attr is None or attr.data_type != 'FLOAT':
        return None
    values = np.empty(len(attr.data), dtype=np.float32)
    attr.data.foreach_get("value", values)
    return values


def _view3d_spaces():
    wm = getattr(bpy.context, "window_manager", None)
    for window in (wm.windows if wm else []):
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                yield area.spaces.active


def show_mask_heatmap(obj, name: str) -> bool:
    """Show a mask as a heatmap color attribute in Solid mode. False if the mask is not an attribute."""
    weights = read_mask(obj, name)
    if weights is None:
        return False
    rgb = np.stack([np.interp(weights, _HEAT_STOPS, _HEAT_RGB[:, c]) for c in range(3)], axis=1)
    rgb[weights <= 0.0] = 0.08            # Outside the mask: dark gray
    rgba = np.column_stack([srgb_to_linear(rgb), np.ones(len(weights), dtype=np.float32)])
    mesh = obj.data
    attr = mesh.color_attributes.get(MASK_VIEW_ATTRIBUTE) or mesh.color_attributes.new(MASK_VIEW_ATTRIBUTE, 'FLOAT_COLOR', 'POINT')
    attr.data.foreach_set("color", rgba.ravel())
    mesh.color_attributes.active_color_name = MASK_VIEW_ATTRIBUTE
    mesh.update()
    for space in _view3d_spaces():
        shading = space.shading
        if "rtg_prev_shading" not in obj:
            obj["rtg_prev_shading"] = f"{shading.type}|{shading.color_type}"
        shading.type = 'SOLID'
        shading.color_type = 'VERTEX'
    obj["rtg_mask_view"] = name
    return True


def hide_mask_heatmap(obj):
    prev = obj.get("rtg_prev_shading", "")
    for space in _view3d_spaces():
        if prev:
            space.shading.type, space.shading.color_type = prev.split("|")
    attr = obj.data.color_attributes.get(MASK_VIEW_ATTRIBUTE)
    if attr is not None:
        obj.data.color_attributes.remove(attr)
    for key in ("rtg_mask_view", "rtg_prev_shading"):
        if key in obj:
            del obj[key]


def refresh_mask_heatmap(obj, updated_names):
    name = obj.get("rtg_mask_view")
    if name and name in updated_names:
        show_mask_heatmap(obj, name)


def apply_color_attribute_to_object(obj, weights_dict: dict[str, np.ndarray]):
    """Pack masks into Float Color attributes (see COLOR_PACKS); channels not given keep their values."""
    mesh = obj.data
    n_verts = len(mesh.vertices)
    for attr_name, channels in COLOR_PACKS.items():
        if not any(name in weights_dict for name in channels):
            continue
        col_attr = mesh.color_attributes.get(attr_name)
        if not col_attr:
            col_attr = mesh.color_attributes.new(name=attr_name, type='FLOAT_COLOR', domain='POINT')
        rgba = np.empty(n_verts * 4, dtype=np.float32)
        col_attr.data.foreach_get("color", rgba)
        rgba = rgba.reshape((n_verts, 4))
        for channel, name in enumerate(channels):
            if name in weights_dict:
                rgba[:, channel] = weights_dict[name]
        col_attr.data.foreach_set("color", rgba.ravel())


def mask_parameters(props) -> dict:
    """Keyword arguments for the core mask functions from the sidebar settings."""
    absolute = props.altitude_reference == "ABSOLUTE"
    return dict(
        max_plant_slope_deg=props.plant_max_slope_deg,
        plant_treeline_ratio=props.plant_treeline_ratio,
        valley_boost=props.plant_valley_boost,
        min_density_threshold=props.plant_density_threshold,
        snow_line_ratio=props.snow_line_ratio,
        treeline_m=props.treeline_m if absolute else None,
        snowline_m=props.snowline_m if absolute else None,
    )


def update_object_mesh_geometry(context, obj, grid: ElevationGrid):
    """Rewrite vertex positions and object scale of a terrain for the current scale settings."""
    props = context.scene.rtg_properties
    mesh_scale, obj_scale = compute_scale_factors(props, context.scene, grid.width_m, grid.height_m)
    new_verts = compute_scaled_vertices(
        grid=grid,
        vertical_scale=props.vertical_scale,
        horizontal_scale=mesh_scale,
        center_origin=props.center_mesh,
        base_elevation_zero=True,
    )
    with ensure_editable_mode(obj):
        mesh = obj.data
        if len(mesh.vertices) == len(new_verts):
            mesh.vertices.foreach_set("co", new_verts.ravel())
            mesh.update()
    obj.scale = (obj_scale, obj_scale, obj_scale)
    sync_scale_dependents(obj, grid, props, mesh_scale, obj_scale)

    if props.auto_adjust_clipping:
        adjust_viewport_and_cameras(context, max(grid.width_m, grid.height_m) * mesh_scale * obj_scale)
    store_settings_on_object(obj, props)
    tag_redraw_all_viewports()


def update_object_masks(obj, grid: ElevationGrid, props, dynamic_only: bool = False):
    """Recompute masks on a terrain. ``dynamic_only`` skips the slider-independent ones."""
    analysis = get_analysis_for_object(obj, grid)
    weights = compute_dynamic_masks(grid, analysis, **mask_parameters(props))
    if not dynamic_only:
        weights = {**analysis.static_masks, **weights}
    apply_masks_to_object(obj, weights, props.masks_as_vertex_groups)
    if props.generate_color_attribute or props.create_material:
        apply_color_attribute_to_object(obj, weights)
    refresh_mask_heatmap(obj, weights)
    store_settings_on_object(obj, props)
    tag_redraw_all_viewports()


def update_active_terrain_quick(context):
    """Live-update callback for scale and relief sliders."""
    if _SYNCING:
        return
    obj = get_active_terrain(context)
    grid = get_grid_for_object(obj)
    if grid:
        update_object_mesh_geometry(context, obj, grid)


def schedule_live_update(kind: str, context):
    """Queue an expensive live update ("masks" or "rocks") for the selected terrain.

    Runs once the slider has been still for LIVE_DEBOUNCE_S, instead of on every drag step.
    """
    global _LAST_REQUEST
    if _SYNCING:
        return
    obj = get_active_terrain(context)
    if not obj:
        return
    _PENDING_UPDATES[kind] = obj.name
    _LAST_REQUEST = time.monotonic()
    if not bpy.app.timers.is_registered(_run_pending_updates):
        bpy.app.timers.register(_run_pending_updates, first_interval=LIVE_DEBOUNCE_S)


def _run_pending_updates():
    wait = LIVE_DEBOUNCE_S - (time.monotonic() - _LAST_REQUEST)
    if wait > 0.01:
        return wait
    scene = bpy.context.scene
    props = scene.rtg_properties
    pending = dict(_PENDING_UPDATES)
    _PENDING_UPDATES.clear()
    for kind, name in pending.items():
        obj = bpy.data.objects.get(name)
        grid = get_grid_for_object(obj)
        if not grid:
            continue
        if kind == "masks" and props.generate_plant_masks:
            has_static = obj.vertex_groups.get("RTG_Water_Flow") or obj.data.attributes.get("RTG_Water_Flow")
            update_object_masks(obj, grid, props, dynamic_only=bool(has_static))
        elif kind == "rocks":
            update_object_geology(scene, obj, grid)
        elif kind == "water":
            water = find_water(obj)
            if water is not None:
                mesh_scale, obj_scale = compute_scale_factors(props, scene, grid.width_m, grid.height_m)
                rebuild_water(water, grid, props, mesh_scale, obj_scale)
                store_settings_on_object(obj, props)
    return None


def update_active_masks_quick(context):
    """Live-update callback for vegetation and snow sliders (debounced)."""
    schedule_live_update("masks", context)


def update_object_geology(scene, obj, grid: ElevationGrid, geology=None):
    """Reassign rocks (from ``geology`` or the stored map units), rewrite attributes, refresh material."""
    props = scene.rtg_properties
    analysis = get_analysis_for_object(obj, grid)
    if geology is None:
        geology = stored_geology(obj, grid)
    rocks = build_rock_layers(props, grid, analysis, geology)
    with ensure_editable_mode(obj):
        write_rock_attributes(obj.data, rocks)
    store_geology_on_object(obj, geology if props.geology_source == "MACROSTRAT" else None, rocks)
    mesh_scale, obj_scale = compute_scale_factors(props, scene, grid.width_m, grid.height_m)
    if props.create_material:
        apply_terrain_material(obj, mesh_scale * obj_scale, props.material_displacement,
                               plan_surface_terms(rocks), props.material_quality)
    apply_terrain_detail(obj, mesh_scale, props.terrain_detail)
    sync_scale_dependents(obj, grid, props, mesh_scale, obj_scale)
    store_settings_on_object(obj, props)
    tag_redraw_all_viewports()


def update_active_rocks_quick(context):
    """Live-update callback for rock settings (no network, debounced)."""
    schedule_live_update("rocks", context)


def update_active_detail_quick(context):
    """Toggle the cliff detail modifier on the selected terrain."""
    if _SYNCING:
        return
    props = context.scene.rtg_properties
    obj = get_active_terrain(context)
    grid = get_grid_for_object(obj)
    if grid:
        mesh_scale, _ = compute_scale_factors(props, context.scene, grid.width_m, grid.height_m)
        apply_terrain_detail(obj, mesh_scale, props.terrain_detail)
        store_settings_on_object(obj, props)


def update_active_material_quick(context):
    """Live-update callback for the displacement mode."""
    if _SYNCING:
        return
    props = context.scene.rtg_properties
    obj = get_active_terrain(context)
    grid = get_grid_for_object(obj)
    mat = obj.active_material if obj else None
    if grid and mat and mat.get("rtg_terrain_material"):
        mesh_scale, obj_scale = compute_scale_factors(props, context.scene, grid.width_m, grid.height_m)
        terms = tuple(t for t in mat.get("rtg_terms", "").split(",") if t)
        apply_terrain_material(obj, mesh_scale * obj_scale, props.material_displacement, terms, props.material_quality)
        store_settings_on_object(obj, props)


def update_active_river_quick(context):
    """Live-update callback for the river sliders (debounced: the network analysis is not instant)."""
    obj = get_active_terrain(context)
    if obj is not None and find_water(obj) is not None:
        schedule_live_update("water", context)


def update_active_haze_quick(context):
    obj = get_active_terrain(context)
    atmosphere = find_atmosphere(obj) if obj else None
    if atmosphere is not None:
        set_haze_visibility(atmosphere, context.scene.rtg_properties.haze_visibility_km, obj.get("rtg_bu_per_m", 1.0))


def adjust_viewport_and_cameras(context, max_dimension_units: float):
    """Ensure 3D Viewport spaces and the active camera clip far enough to show the terrain."""
    required_clip = max(1000.0, float(max_dimension_units) * 3.0)
    screen = getattr(context, "screen", None)
    if screen:
        for area in screen.areas:
            if area.type == 'VIEW_3D':
                for space in area.spaces:
                    if space.type == 'VIEW_3D' and space.clip_end < required_clip:
                        space.clip_end = required_clip

    camera = context.scene.camera
    if camera and hasattr(camera.data, "clip_end") and camera.data.clip_end < required_clip:
        camera.data.clip_end = required_clip


def frame_active_object(context):
    """Center and frame the view on the active object in the first 3D viewport."""
    screen = getattr(context, "screen", None)
    if not screen:
        return
    for area in screen.areas:
        if area.type == 'VIEW_3D':
            region = next((r for r in area.regions if r.type == 'WINDOW'), None)
            if region:
                with context.temp_override(area=area, region=region):
                    bpy.ops.view3d.view_selected()
                return


def resolve_zoom(props) -> int:
    """Tile zoom used for the AWS provider (automatic or manual)."""
    if props.auto_zoom:
        return recommend_zoom_for_area(
            props.area_width_km, props.area_height_km,
            target_resolution=int(props.grid_resolution), lat_deg=props.latitude,
        )
    return props.zoom_level


if BLENDER_AVAILABLE:
    @persistent
    def on_depsgraph_update(scene, depsgraph):
        """Load a terrain's settings into the sidebar when it becomes the active object."""
        global _LAST_ACTIVE_TERRAIN
        view_layer = bpy.context.view_layer
        active = view_layer.objects.active if view_layer else None
        key = active.name_full if active and active.get("rtg_is_terrain") else ""
        if key == _LAST_ACTIVE_TERRAIN:
            return
        _LAST_ACTIVE_TERRAIN = key
        if key and hasattr(scene, "rtg_properties"):
            load_settings_from_object(active, scene.rtg_properties)


    class RTG_OT_SearchLocation(bpy.types.Operator):
        """Search a place name with OpenStreetMap Nominatim and fill in its coordinates and extent"""
        bl_idname = "rtg.search_location"
        bl_label = "Search Place"
        bl_options = {'REGISTER', 'UNDO'}

        def execute(self, context):
            props = context.scene.rtg_properties
            query = props.location_search.strip()
            if not query:
                self.report({'WARNING'}, "Enter a place name to search.")
                return {'CANCELLED'}

            try:
                results = search_location(query, limit=3)
            except Exception as e:
                self.report({'ERROR'}, f"Search failed: {e}")
                return {'CANCELLED'}

            if not results:
                props.search_status = "No results found"
                self.report({'WARNING'}, "No matching locations found.")
                return {'CANCELLED'}

            top = results[0]
            props.latitude = top.latitude
            props.longitude = top.longitude

            # Size the area from the place's bounding box
            w_m = haversine_distance_m(top.latitude, top.bbox.min_lon, top.latitude, top.bbox.max_lon)
            h_m = haversine_distance_m(top.bbox.min_lat, top.longitude, top.bbox.max_lat, top.longitude)
            props.area_width_km = round(max(1.0, min(100.0, w_m / 1000.0)), 1)
            props.area_height_km = round(max(1.0, min(100.0, h_m / 1000.0)), 1)

            short_name = (top.display_name[:55] + "..") if len(top.display_name) > 55 else top.display_name
            props.search_status = short_name
            self.report({'INFO'}, f"Located: {top.display_name}")
            return {'FINISHED'}


    class RTG_OT_GenerateTerrain(bpy.types.Operator):
        """Download elevation data for the area and build the 3D terrain"""
        bl_idname = "rtg.generate_terrain"
        bl_label = "Generate Terrain"
        bl_options = {'REGISTER', 'UNDO'}

        def execute(self, context):
            props = context.scene.rtg_properties
            cache_dir = get_cache_directory(context)

            if props.provider == "OPENTOPOGRAPHY":
                api_key = get_opentopography_key(context)
                if not api_key:
                    self.report({'ERROR'}, "OpenTopography needs an API key: add it in Preferences > Add-ons > Real Terrain Generator.")
                    return {'CANCELLED'}
                provider = OpenTopographyProvider(api_key=api_key, dem_type=props.opentopography_dataset)
            else:
                provider = AWSTerrariumProvider()

            bbox = bbox_from_center(
                center_lat=props.latitude,
                center_lon=props.longitude,
                width_km=props.area_width_km,
                height_km=props.area_height_km,
            )
            zoom = resolve_zoom(props)
            target_res = int(props.grid_resolution)

            wm = context.window_manager
            wm.progress_begin(0, 5)
            try:
                wm.progress_update(1)
                try:
                    grid = provider.fetch_elevation_grid(
                        bbox=bbox, zoom=zoom, target_resolution=target_res, cache_dir=cache_dir,
                    )
                except Exception as e:
                    self.report({'ERROR'}, f"Failed to download elevation data: {e}")
                    return {'CANCELLED'}

                wm.progress_update(2)
                mesh_scale, obj_scale = compute_scale_factors(props, context.scene, grid.width_m, grid.height_m)
                analysis = analyze_terrain(grid) if (props.generate_plant_masks or props.create_material) else None
                mesh_data = build_terrain_mesh_data(
                    grid=grid,
                    vertical_scale=props.vertical_scale,
                    horizontal_scale=mesh_scale,
                    center_origin=props.center_mesh,
                    base_elevation_zero=True,
                    analysis=analysis,
                    compute_masks=props.generate_plant_masks,
                    **mask_parameters(props),
                )

                wm.progress_update(3)
                obj = self._create_terrain_object(context, props, grid, obj_scale, mesh_data)
                _GRID_CACHE[obj.data.session_uid] = (grid, analysis)
                if mesh_data.vertex_weights:
                    apply_masks_to_object(obj, mesh_data.vertex_weights, props.masks_as_vertex_groups)
                    if props.generate_color_attribute or props.create_material:
                        apply_color_attribute_to_object(obj, mesh_data.vertex_weights)

                wm.progress_update(4)
                write_position_attribute(obj.data, grid)
                geology = None
                try:
                    geology = fetch_geology(props, grid, cache_dir)
                except Exception as e:
                    self.report({'WARNING'}, f"Geologic map unavailable ({e}); using {props.default_rock}.")
                update_object_geology(context.scene, obj, grid, geology)
            finally:
                wm.progress_end()

            final_span_bu = max(grid.width_m, grid.height_m) * mesh_scale * obj_scale
            if props.auto_adjust_clipping:
                adjust_viewport_and_cameras(context, final_span_bu)
            if props.frame_terrain:
                frame_active_object(context)

            src_rows, src_cols = grid.metadata.get("source_shape", grid.shape)
            rows, cols = grid.shape
            note = " (data upsampled: increase zoom or lower resolution)" if max(rows, cols) > max(src_rows, src_cols) else ""
            self.report(
                {'INFO'},
                f"Generated '{obj.name}': {grid.width_m / 1000.0:.1f} x {grid.height_m / 1000.0:.1f} km, "
                f"{cols} x {rows} vertices from {src_cols} x {src_rows} source samples{note}",
            )
            return {'FINISHED'}

        @staticmethod
        def _create_terrain_object(context, props, grid, obj_scale, mesh_data):
            obj_name = f"Terrain_{props.latitude:.2f}_{props.longitude:.2f}"
            mesh = create_grid_mesh(f"{obj_name}_Mesh", mesh_data.vertices, mesh_data.faces, mesh_data.uvs)
            elev_attr = mesh.attributes.new(ELEVATION_ATTRIBUTE, 'FLOAT', 'POINT')
            elev_attr.data.foreach_set("value", grid.elevation.ravel().astype(np.float32))

            obj = bpy.data.objects.new(obj_name, mesh)
            obj.scale = (obj_scale, obj_scale, obj_scale)
            context.collection.objects.link(obj)

            rows, cols = grid.shape
            obj["rtg_is_terrain"] = True
            obj["rtg_grid_rows"] = rows
            obj["rtg_grid_cols"] = cols
            obj["rtg_bbox"] = tuple(grid.bbox)
            obj["rtg_width_m"] = grid.width_m
            obj["rtg_height_m"] = grid.height_m
            obj["rtg_elevation_min_m"] = grid.min_elevation_m
            obj["rtg_elevation_max_m"] = grid.max_elevation_m
            obj["rtg_provider"] = grid.provider_name
            obj["rtg_attribution"] = grid.attribution
            store_settings_on_object(obj, props)

            for other in context.selected_objects:
                other.select_set(False)
            obj.select_set(True)
            context.view_layer.objects.active = obj
            # The new terrain already matches the sidebar; skip the auto-load on selection
            global _LAST_ACTIVE_TERRAIN
            _LAST_ACTIVE_TERRAIN = obj.name_full
            return obj


    class RTG_OT_UpdateActiveTerrain(bpy.types.Operator):
        """Apply the scale and relief settings to the selected terrain"""
        bl_idname = "rtg.update_active_terrain"
        bl_label = "Apply Scale & Relief"
        bl_options = {'REGISTER', 'UNDO'}

        @classmethod
        def poll(cls, context):
            return get_active_terrain(context) is not None

        def execute(self, context):
            obj = get_active_terrain(context)
            grid = get_grid_for_object(obj)
            if not grid:
                self.report({'ERROR'}, "This terrain has no stored elevation data; generate it again.")
                return {'CANCELLED'}
            update_object_mesh_geometry(context, obj, grid)
            return {'FINISHED'}


    class RTG_OT_RecalculateMasks(bpy.types.Operator):
        """Recompute every terrain mask on the selected terrain"""
        bl_idname = "rtg.recalculate_masks"
        bl_label = "Recalculate Masks"
        bl_options = {'REGISTER', 'UNDO'}

        @classmethod
        def poll(cls, context):
            return get_active_terrain(context) is not None

        def execute(self, context):
            obj = get_active_terrain(context)
            grid = get_grid_for_object(obj)
            if not grid:
                self.report({'ERROR'}, "This terrain has no stored elevation data; generate it again.")
                return {'CANCELLED'}
            update_object_masks(obj, grid, context.scene.rtg_properties)
            return {'FINISHED'}


    class RTG_OT_UpdateGeology(bpy.types.Operator):
        """Download the geologic map again (if selected) and rebuild rocks and material on the selected terrain"""
        bl_idname = "rtg.update_geology"
        bl_label = "Update Geology"
        bl_options = {'REGISTER', 'UNDO'}

        @classmethod
        def poll(cls, context):
            return get_active_terrain(context) is not None

        def execute(self, context):
            props = context.scene.rtg_properties
            obj = get_active_terrain(context)
            grid = get_grid_for_object(obj)
            if not grid:
                self.report({'ERROR'}, "This terrain has no stored elevation data; generate it again.")
                return {'CANCELLED'}
            write_position_attribute(obj.data, grid)
            try:
                geology = fetch_geology(props, grid, get_cache_directory(context))
            except Exception as e:
                self.report({'WARNING'}, f"Geologic map unavailable ({e}); using {props.default_rock}.")
                geology = None
            update_object_geology(context.scene, obj, grid, geology)
            units = len(geology.units) if geology else 0
            self.report({'INFO'}, f"Geology updated ({units} map units)" if units else "Geology updated")
            return {'FINISHED'}


    class RTG_OT_AddWater(bpy.types.Operator):
        """Add or update the water surface (river, lake, reservoir) at the Water Level altitude"""
        bl_idname = "rtg.add_water"
        bl_label = "Water Surface"
        bl_options = {'REGISTER', 'UNDO'}

        @classmethod
        def poll(cls, context):
            return get_active_terrain(context) is not None

        def execute(self, context):
            props = context.scene.rtg_properties
            terrain = get_active_terrain(context)
            grid = get_grid_for_object(terrain)
            if not grid:
                self.report({'ERROR'}, "This terrain has no stored elevation data; generate it again.")
                return {'CANCELLED'}
            level = props.water_level_m
            if build_water_mesh(grid, level, river=river_for_grid(grid, props)) is None:
                if props.water_mode == "RIVER":
                    self.report({'WARNING'}, "No river found; lower Channel Area or raise the Water Level.")
                else:
                    self.report({'WARNING'}, f"Nothing lies below {level:.0f} m; use the eyedropper to estimate the level.")
                return {'CANCELLED'}

            new_water = find_water(terrain) is None
            water = ensure_water_object(terrain, context.scene)
            water["rtg_water_level_m"] = level
            mesh_scale, obj_scale = compute_scale_factors(props, context.scene, grid.width_m, grid.height_m)
            rebuild_water(water, grid, props, mesh_scale, obj_scale)
            set_material_water_level(terrain, level)
            rocks = find_rocks(terrain)
            if rocks is not None:
                update_rocks(rocks, mesh_scale, level)
            if new_water:
                azimuth = estimate_flow_azimuth(grid, get_analysis_for_object(terrain, grid).static_masks["RTG_Water_Flow"])
                if azimuth is not None:
                    set_flow_direction(water, azimuth)
            store_settings_on_object(terrain, props)
            return {'FINISHED'}


    class RTG_OT_ScatterRocks(bpy.types.Operator):
        """Scatter boulders, scree and cobbles where the geology sheds them (Geometry Nodes, editable live)"""
        bl_idname = "rtg.scatter_rocks"
        bl_label = "Scatter Rocks"
        bl_options = {'REGISTER', 'UNDO'}

        @classmethod
        def poll(cls, context):
            return get_active_terrain(context) is not None

        def execute(self, context):
            props = context.scene.rtg_properties
            terrain = get_active_terrain(context)
            grid = get_grid_for_object(terrain)
            if not grid:
                self.report({'ERROR'}, "This terrain has no stored elevation data; generate it again.")
                return {'CANCELLED'}
            if terrain.data.attributes.get("RTG_Boulder_Density") is None:
                self.report({'ERROR'}, "No rock data on this terrain; press Update Geology first.")
                return {'CANCELLED'}
            mesh_scale, _ = compute_scale_factors(props, context.scene, grid.width_m, grid.height_m)
            water = find_water(terrain)
            level = float(water["rtg_water_level_m"]) if water is not None and "rtg_water_level_m" in water else -100000.0
            ensure_rocks(context, terrain, mesh_scale, level)
            if context.scene.camera is None:
                self.report({'INFO'}, "Rocks scattered everywhere; add a camera to limit them by Camera Range.")
            return {'FINISHED'}


    class RTG_OT_RemoveRocks(bpy.types.Operator):
        """Remove the rock scatter of the selected terrain (the rock library is kept)"""
        bl_idname = "rtg.remove_rocks"
        bl_label = "Remove Rocks"
        bl_options = {'REGISTER', 'UNDO'}

        @classmethod
        def poll(cls, context):
            obj = get_active_terrain(context)
            return obj is not None and find_rocks(obj) is not None

        def execute(self, context):
            rocks = find_rocks(get_active_terrain(context))
            mesh = rocks.data
            bpy.data.objects.remove(rocks)
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)
            return {'FINISHED'}


    class RTG_OT_EstimateWaterLevel(bpy.types.Operator):
        """Set the water level just above the lowest ground (DEMs record the water surface there)"""
        bl_idname = "rtg.estimate_water_level"
        bl_label = "Estimate Water Level"
        bl_options = {'REGISTER', 'UNDO'}

        @classmethod
        def poll(cls, context):
            return get_active_terrain(context) is not None

        def execute(self, context):
            grid = get_grid_for_object(get_active_terrain(context))
            if grid:
                context.scene.rtg_properties.water_level_m = estimate_water_level(grid.elevation)
            return {'FINISHED'}


    class RTG_OT_EstimateClimateLines(bpy.types.Operator):
        """Set tree line and snow line from the latitude (rough global trend; adjust for local climate)"""
        bl_idname = "rtg.estimate_climate_lines"
        bl_label = "Estimate from Latitude"
        bl_options = {'REGISTER', 'UNDO'}

        def execute(self, context):
            props = context.scene.rtg_properties
            treeline, snowline = estimate_climate_lines(props.latitude)
            props.treeline_m = treeline
            props.snowline_m = snowline
            self.report({'INFO'}, f"Tree line ~{treeline:.0f} m, snow line ~{snowline:.0f} m at latitude {props.latitude:.1f}°")
            return {'FINISHED'}


    class RTG_OT_SunFromTime(bpy.types.Operator):
        """Place the sun for the terrain's latitude, day of the year and local solar time"""
        bl_idname = "rtg.sun_from_time"
        bl_label = "Sun from Time"
        bl_options = {'REGISTER', 'UNDO'}

        def execute(self, context):
            props = context.scene.rtg_properties
            elevation, azimuth = solar_position(props.latitude, props.sun_day, props.sun_hour)
            props.sun_elevation_deg = max(elevation, -5.0)
            props.sun_azimuth_deg = azimuth
            if elevation < 0.0:
                self.report({'WARNING'}, f"The sun is below the horizon ({elevation:.1f}°) at that time")
            return {'FINISHED'}


    class RTG_OT_SetupScene(bpy.types.Operator):
        """Physical sky with the sun, aerial-perspective haze, a landscape camera and render settings for the selected terrain"""
        bl_idname = "rtg.setup_scene"
        bl_label = "Setup Realistic Scene"
        bl_options = {'REGISTER', 'UNDO'}

        @classmethod
        def poll(cls, context):
            return get_active_terrain(context) is not None

        def execute(self, context):
            props = context.scene.rtg_properties
            scene = context.scene
            terrain = get_active_terrain(context)
            bu_per_m = terrain.get("rtg_bu_per_m", 1.0)

            ensure_sky(scene)
            update_sky(scene, props.sun_elevation_deg, props.sun_azimuth_deg, props.sky_aerosols,
                       altitude_m=terrain.get("rtg_elevation_min_m", 0.0))
            hidden = hide_sun_lamps(scene)

            camera = place_camera(context, terrain, props.sun_azimuth_deg) if props.place_camera else scene.camera
            if props.aerial_perspective:
                top_z = camera.matrix_world.translation.z if camera else terrain.matrix_world.translation.z
                ensure_atmosphere(scene, terrain, props.haze_visibility_km, bu_per_m, top_z)
            else:
                remove_atmosphere(terrain)

            apply_render_preset(scene, props.render_preset)
            if props.create_material and props.material_quality != props.render_preset:
                props.material_quality = props.render_preset
            adjust_viewport_and_cameras(context, max(terrain.dimensions.x, terrain.dimensions.y))

            message = "Scene ready"
            if hidden:
                message += f"; hid {hidden} sun lamp(s), the sky has its own sun"
            self.report({'INFO'}, message)
            return {'FINISHED'}


    class RTG_OT_ExitMaskView(bpy.types.Operator):
        """Stop showing the mask heatmap and restore the viewport shading"""
        bl_idname = "rtg.exit_mask_view"
        bl_label = "Exit Mask View"
        bl_options = {'REGISTER', 'UNDO'}

        @classmethod
        def poll(cls, context):
            return get_active_terrain(context) is not None

        def execute(self, context):
            obj = get_active_terrain(context)
            if obj.mode == 'WEIGHT_PAINT':
                bpy.ops.object.mode_set(mode='OBJECT')
            hide_mask_heatmap(obj)
            tag_redraw_all_viewports()
            return {'FINISHED'}


    class RTG_OT_SetActiveMask(bpy.types.Operator):
        """Show a terrain mask as a heatmap in the viewport"""
        bl_idname = "rtg.set_active_mask"
        bl_label = "View Mask"
        bl_options = {'REGISTER', 'UNDO'}

        mask_name: bpy.props.StringProperty(name="Mask Name", default="RTG_Plant_Density")

        @classmethod
        def description(cls, context, properties):
            return MASK_DESCRIPTIONS.get(properties.mask_name, cls.__doc__)

        @classmethod
        def poll(cls, context):
            return get_active_terrain(context) is not None

        def execute(self, context):
            obj = get_active_terrain(context)
            if show_mask_heatmap(obj, self.mask_name):
                tag_redraw_all_viewports()
                return {'FINISHED'}
            # Masks stored as vertex groups: inspect them in Weight Paint mode
            vg = obj.vertex_groups.get(self.mask_name)
            if not vg:
                self.report({'WARNING'}, f"'{obj.name}' has no '{self.mask_name}' mask; press Recalculate Masks.")
                return {'CANCELLED'}
            context.view_layer.objects.active = obj
            obj.select_set(True)
            obj.vertex_groups.active_index = vg.index
            if obj.mode != 'WEIGHT_PAINT':
                bpy.ops.object.mode_set(mode='WEIGHT_PAINT')
            return {'FINISHED'}


    class RTG_OT_ExportHeightmap(bpy.types.Operator, ExportHelper):
        """Export the selected terrain's elevation as a 16-bit grayscale PNG (for Gaea, Unreal, Unity, World Machine, displacement...)"""
        bl_idname = "rtg.export_heightmap"
        bl_label = "Export Heightmap"
        bl_options = {'REGISTER'}

        filename_ext = ".png"
        filter_glob: bpy.props.StringProperty(default="*.png", options={'HIDDEN'})

        write_metadata: bpy.props.BoolProperty(
            name="Write Metadata (.json)",
            description="Save min/max elevation, real dimensions and coordinates next to the image, needed to rebuild true scale elsewhere",
            default=True,
        )

        @classmethod
        def poll(cls, context):
            return get_active_terrain(context) is not None

        def invoke(self, context, event):
            obj = get_active_terrain(context)
            self.filepath = bpy.path.clean_name(f"{obj.name}_heightmap") + self.filename_ext
            return ExportHelper.invoke(self, context, event)

        def execute(self, context):
            obj = get_active_terrain(context)
            grid = get_grid_for_object(obj)
            if not grid:
                self.report({'ERROR'}, "This terrain has no stored elevation data; generate it again.")
                return {'CANCELLED'}

            path, min_e, max_e = save_heightmap_png16(grid.elevation, Path(bpy.path.abspath(self.filepath)))
            if self.write_metadata:
                rows, cols = grid.shape
                meta = {
                    "elevation_min_m": min_e,
                    "elevation_max_m": max_e,
                    "width_m": grid.width_m,
                    "height_m": grid.height_m,
                    "pixels": [cols, rows],
                    "bbox_wgs84": dict(grid.bbox._asdict()),
                    "source": grid.provider_name,
                    "attribution": grid.attribution,
                }
                path.with_suffix(".json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
            self.report({'INFO'}, f"Saved {path.name} ({min_e:.0f} m = black, {max_e:.0f} m = white)")
            return {'FINISHED'}


    class RTG_OT_OpenPreferences(bpy.types.Operator):
        """Open the add-on preferences (API keys and download cache)"""
        bl_idname = "rtg.open_preferences"
        bl_label = "Add-on Preferences"
        bl_options = {'REGISTER'}

        def execute(self, context):
            bpy.ops.screen.userpref_show('INVOKE_DEFAULT')
            context.preferences.active_section = 'ADDONS'
            context.window_manager.addon_search = "Real Terrain"
            return {'FINISHED'}


    class RTG_OT_ClearCache(bpy.types.Operator):
        """Delete downloaded elevation tiles from the cache folder (terrains in your files are not affected)"""
        bl_idname = "rtg.clear_cache"
        bl_label = "Clear Download Cache"
        bl_options = {'REGISTER'}

        def invoke(self, context, event):
            return context.window_manager.invoke_confirm(self, event)

        def execute(self, context):
            cache_dir = get_cache_directory(context)
            removed = 0
            if cache_dir.is_dir():
                for pattern in ("terrarium_*.png", "opentopo_*.asc", "grid_*.npz", "heightmap_*.png"):
                    for f in cache_dir.glob(pattern):
                        f.unlink(missing_ok=True)
                        removed += 1
            self.report({'INFO'}, f"Removed {removed} cached files")
            return {'FINISHED'}


    def menu_export_heightmap(self, context):
        self.layout.operator(RTG_OT_ExportHeightmap.bl_idname, text="Real Terrain Heightmap (.png)")


    OPERATOR_CLASSES = (
        RTG_OT_SearchLocation,
        RTG_OT_GenerateTerrain,
        RTG_OT_UpdateActiveTerrain,
        RTG_OT_RecalculateMasks,
        RTG_OT_UpdateGeology,
        RTG_OT_AddWater,
        RTG_OT_EstimateWaterLevel,
        RTG_OT_ScatterRocks,
        RTG_OT_RemoveRocks,
        RTG_OT_EstimateClimateLines,
        RTG_OT_SunFromTime,
        RTG_OT_SetupScene,
        RTG_OT_ExitMaskView,
        RTG_OT_SetActiveMask,
        RTG_OT_ExportHeightmap,
        RTG_OT_OpenPreferences,
        RTG_OT_ClearCache,
    )
else:
    OPERATOR_CLASSES = ()
