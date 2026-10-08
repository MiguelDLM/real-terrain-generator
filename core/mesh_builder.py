"""Constructs 3D mesh geometry, UVs, and topographic vertex weight masks."""

from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Tuple
import numpy as np

from .dem_sources.base import ElevationGrid
from .heightmap import calculate_slope_and_aspect, calculate_topographic_curvature

# Altitude bands (meters) used when tree/snow lines are given as real altitudes
TREELINE_TRANSITION_M = 300.0   # Forest thins out over this band below the tree line (krummholz)
SNOW_RAMP_M = 300.0             # Snow cover goes from patchy to full over this band above the snow line
SNOW_SHADE_SHIFT_M = 150.0      # Pole-facing slopes hold snow as if this much higher

# Masks that do not depend on any user parameter (computed once per terrain)
STATIC_MASK_NAMES = ("RTG_Elevation", "RTG_Slope", "RTG_Valley", "RTG_Water_Flow", "RTG_Wetness", "RTG_Talus", "RTG_Roughness")
# Masks driven by the vegetation / snow sliders (cheap to recompute live)
DYNAMIC_MASK_NAMES = ("RTG_Plant_Density", "RTG_Snow")


@dataclass
class TerrainMeshData:
    """Pre-computed geometric buffers ready for Blender mesh creation or export."""
    vertices: np.ndarray            # (N, 3) float32 coordinates [X, Y, Z]
    faces: np.ndarray               # (M, 4) int32 quad indices
    uvs: np.ndarray                 # (N, 2) float32 UV coordinates [U, V]
    vertex_weights: Dict[str, np.ndarray]  # Topographic masks for plant distribution
    width_m: float
    height_m: float
    elevation_min_m: float
    elevation_max_m: float
    vertical_relief_m: float


def compute_scaled_vertices(
    grid: ElevationGrid,
    vertical_scale: float = 1.0,
    horizontal_scale: float = 1.0,
    center_origin: bool = True,
    base_elevation_zero: bool = True,
) -> np.ndarray:
    """Compute (N, 3) vertex array with scaled dimensions and vertical relief."""
    elev = grid.elevation
    rows, cols = elev.shape

    mesh_w = grid.width_m * float(horizontal_scale)
    mesh_h = grid.height_m * float(horizontal_scale)

    x_coords = np.linspace(0.0, mesh_w, cols, dtype=np.float32)
    y_coords = np.linspace(mesh_h, 0.0, rows, dtype=np.float32)

    if center_origin:
        x_coords -= (mesh_w / 2.0)
        y_coords -= (mesh_h / 2.0)

    xx, yy = np.meshgrid(x_coords, y_coords)

    base_z = grid.min_elevation_m if base_elevation_zero else 0.0
    zz = (elev - base_z) * float(vertical_scale) * float(horizontal_scale)

    return np.column_stack([xx.ravel(), yy.ravel(), zz.ravel()]).astype(np.float32)


def compute_flow_accumulation(elev: np.ndarray, cell_x_m: float = 30.0, cell_y_m: float = 30.0) -> np.ndarray:
    """Compute D8 hydrological flow accumulation matrix in topological order."""
    rows, cols = elev.shape
    shifts = [
        (-1, -1, float(np.hypot(cell_y_m, cell_x_m))),
        (-1,  0, float(cell_y_m)),
        (-1,  1, float(np.hypot(cell_y_m, cell_x_m))),
        ( 0, -1, float(cell_x_m)),
        ( 0,  1, float(cell_x_m)),
        ( 1, -1, float(np.hypot(cell_y_m, cell_x_m))),
        ( 1,  0, float(cell_y_m)),
        ( 1,  1, float(np.hypot(cell_y_m, cell_x_m))),
    ]
    max_slope = np.zeros((rows, cols), dtype=np.float32)
    receiver = np.full((rows, cols), -1, dtype=np.int32)
    r_coords, c_coords = np.indices((rows, cols))

    for dr, dc, dist in shifts:
        nr = np.clip(r_coords + dr, 0, rows - 1)
        nc = np.clip(c_coords + dc, 0, cols - 1)
        drop = (elev - elev[nr, nc]) / dist
        valid = (r_coords + dr >= 0) & (r_coords + dr < rows) & (c_coords + dc >= 0) & (c_coords + dc < cols)
        better = valid & (drop > max_slope) & (drop > 0)
        max_slope[better] = drop[better]
        receiver[better] = (nr * cols + nc)[better]

    # Highest cells first; plain Python lists are ~5x faster than NumPy scalar indexing here
    flat_rcv = receiver.ravel().tolist()
    order = np.argsort(-elev.ravel(), kind="stable").tolist()
    acc = [1.0] * (rows * cols)
    for idx in order:
        r = flat_rcv[idx]
        if r >= 0:
            acc[r] += acc[idx]
    return np.asarray(acc, dtype=np.float32).reshape((rows, cols))


def compute_topographic_wetness_index(flow_acc: np.ndarray, slope_deg: np.ndarray, cell_x_m: float = 30.0) -> np.ndarray:
    """Compute Topographic Wetness Index (TWI) ln(a / tan(beta))."""
    slope_rad = np.radians(np.clip(slope_deg, 0.1, 89.0))
    As = flow_acc * float(cell_x_m)
    tan_beta = np.tan(slope_rad)
    twi = np.log(np.maximum(1.0, As) / (tan_beta + 0.01))
    p5 = float(np.percentile(twi, 5))
    p95 = float(np.percentile(twi, 95))
    return np.clip((twi - p5) / max(1e-3, p95 - p5), 0.0, 1.0).astype(np.float32)


def compute_talus_scree_mask(slope_deg: np.ndarray) -> np.ndarray:
    """Model scree/talus deposition aprons at the angle of repose (30-38 deg) below steep cliffs (>40 deg)."""
    cliff_shed = np.clip((slope_deg - 40.0) / 20.0, 0.0, 1.0)
    repose_band = np.exp(-((slope_deg - 34.0)**2) / (2.0 * (5.5**2)))

    pad = np.pad(cliff_shed, 1, mode='edge')
    debris = (pad[:-2, 1:-1] + pad[2:, 1:-1] + pad[1:-1, :-2] + pad[1:-1, 2:] + pad[1:-1, 1:-1] * 4) / 8.0
    for _ in range(3):
        pad = np.pad(debris, 1, mode='edge')
        debris = (pad[:-2, 1:-1] + pad[2:, 1:-1] + pad[1:-1, :-2] + pad[1:-1, 2:] + pad[1:-1, 1:-1] * 4) / 8.0

    return np.clip(debris * repose_band * 2.5, 0.0, 1.0).astype(np.float32)


def compute_structural_snow_mask(
    elev: np.ndarray,
    slope_deg: np.ndarray,
    curvature: np.ndarray,
    snow_line_ratio: float = 0.65,
    snowline_m: float | None = None,
    shade: np.ndarray | None = None,
) -> np.ndarray:
    """Compute structural snowpack accumulation based on altitude, slope avalanche threshold, and concavity.

    The snow line is ``snowline_m`` (absolute meters) when given, otherwise ``snow_line_ratio``
    of the local relief. ``shade`` (0..1, pole-facing exposure) lowers the effective snow line
    by up to SNOW_SHADE_SHIFT_M on shaded slopes, where snow lasts longer.
    """
    e_min, e_max = float(elev.min()), float(elev.max())
    relief = max(1.0, e_max - e_min)
    if snowline_m is None:
        sl = np.clip(float(snow_line_ratio), 0.05, 0.95)
        line_m = e_min + sl * relief
        ramp_m = max(0.05, 1.0 - sl) * relief
    else:
        line_m = float(snowline_m)
        ramp_m = SNOW_RAMP_M
    effective_elev = elev if shade is None else elev + shade * SNOW_SHADE_SHIFT_M
    alt_factor = np.clip((effective_elev - line_m) / ramp_m, 0.0, 1.0)
    # Steep cliffs (>45-50 deg) slough off snow
    slope_hold = np.clip(1.0 - (slope_deg - 30.0) / 20.0, 0.0, 1.0)
    # Concave cirques and hollows trap drifting snow
    curv_min = float(np.percentile(curvature, 2))
    curv_max = float(np.percentile(curvature, 98))
    concavity = np.clip((curvature - curv_min) / max(1e-4, curv_max - curv_min), 0.0, 1.0)
    trap_boost = 0.7 + 0.3 * concavity
    return np.clip(alt_factor * slope_hold * trap_boost, 0.0, 1.0).astype(np.float32)


def compute_pole_facing_shade(slope_deg: np.ndarray, aspect_rad: np.ndarray, latitude_deg: float) -> np.ndarray:
    """0..1 exposure of slopes facing the pole (north in the northern hemisphere), weighted by steepness.

    ``aspect_rad`` is the uphill gradient direction counterclockwise from East, so the
    slope faces the opposite way; its northward component is ``-sin(aspect)``.
    """
    facing_north = -np.sin(aspect_rad)
    facing_pole = facing_north if latitude_deg >= 0.0 else -facing_north
    steepness = np.clip(slope_deg / 30.0, 0.0, 1.0)
    return np.clip(facing_pole, 0.0, 1.0) * steepness


def compute_terrain_ruggedness_index(elev: np.ndarray) -> np.ndarray:
    """Compute Topographic Ruggedness Index (TRI, Riley et al. 1999) from 8-neighbor height variance."""
    rows, cols = elev.shape
    pad = np.pad(elev, 1, mode='edge')
    diff_sq_sum = np.zeros_like(elev)
    shifts = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
    for dr, dc in shifts:
        diff_sq_sum += (elev - pad[1+dr:1+dr+rows, 1+dc:1+dc+cols])**2
    tri = np.sqrt(diff_sq_sum / 8.0)
    tri_p98 = float(np.percentile(tri, 98))
    return np.clip(tri / max(1e-3, tri_p98), 0.0, 1.0).astype(np.float32)


@dataclass
class TerrainAnalysis:
    """Parameter-independent derivatives of an elevation grid, reused by live slider updates."""
    slope_deg: np.ndarray
    curvature: np.ndarray
    shade: np.ndarray
    static_masks: Dict[str, np.ndarray]


def analyze_terrain(grid: ElevationGrid) -> TerrainAnalysis:
    """Compute slope, curvature, exposure and every mask that does not depend on user parameters.

    This holds the expensive steps (D8 flow accumulation, TWI, talus, TRI); the vegetation
    and snow sliders only re-run :func:`compute_dynamic_masks`.
    """
    elev = grid.elevation
    rows, cols = elev.shape
    cell_x_m = grid.width_m / max(1, cols - 1)
    cell_y_m = grid.height_m / max(1, rows - 1)

    slope_deg, aspect_rad = calculate_slope_and_aspect(elev, cell_x_m, cell_y_m)
    curvature = calculate_topographic_curvature(elev)
    shade = compute_pole_facing_shade(slope_deg, aspect_rad, grid.bbox.center[0])

    elev_range = max(0.001, grid.elevation_range_m)
    elevation_mask = np.clip((elev - grid.min_elevation_m) / elev_range, 0.0, 1.0)

    curv_min = float(np.percentile(curvature, 2))
    curv_max = float(np.percentile(curvature, 98))
    valley_mask = np.clip((curvature - curv_min) / max(1e-4, curv_max - curv_min), 0.0, 1.0)

    # 1. Flow Accumulation (D8)
    flow_acc = compute_flow_accumulation(elev, cell_x_m, cell_y_m)
    flow_convergent = np.maximum(0.0, flow_acc - 3.0)
    log_flow = np.log1p(flow_convergent)
    p_flow = float(np.percentile(log_flow[log_flow > 0], 99.5)) if np.any(log_flow > 0) else 1.0
    flow_mask = np.clip(log_flow / max(1.0, p_flow), 0.0, 1.0)

    static_masks = {
        "RTG_Elevation": elevation_mask,
        "RTG_Slope": np.clip(slope_deg / 90.0, 0.0, 1.0),
        "RTG_Valley": valley_mask,
        "RTG_Water_Flow": flow_mask,
        # 2. Topographic Wetness Index (TWI)
        "RTG_Wetness": compute_topographic_wetness_index(flow_acc, slope_deg, cell_x_m),
        # 3. Talus / Scree Deposition Aprons
        "RTG_Talus": compute_talus_scree_mask(slope_deg),
        # 4. Topographic Ruggedness Index (TRI)
        "RTG_Roughness": compute_terrain_ruggedness_index(elev),
    }
    static_masks = {k: v.ravel().astype(np.float32) for k, v in static_masks.items()}
    return TerrainAnalysis(slope_deg=slope_deg, curvature=curvature, shade=shade, static_masks=static_masks)


def compute_dynamic_masks(
    grid: ElevationGrid,
    analysis: TerrainAnalysis,
    max_plant_slope_deg: float = 35.0,
    plant_treeline_ratio: float = 0.8,
    valley_boost: float = 0.3,
    min_density_threshold: float = 0.02,
    snow_line_ratio: float = 0.65,
    treeline_m: float | None = None,
    snowline_m: float | None = None,
) -> Dict[str, np.ndarray]:
    """Compute the slider-driven masks (plant density and snow) from a cached analysis.

    Tree and snow lines are real altitudes in meters when ``treeline_m`` / ``snowline_m``
    are given, otherwise ratios (0 = lowest point, 1 = highest point) of the local relief.
    """
    elev = grid.elevation
    max_slope = max(1.0, float(max_plant_slope_deg))
    flat_ground_mask = (1.0 - np.clip(analysis.slope_deg / max_slope, 0.0, 1.0)).ravel()

    if treeline_m is None:
        tl = max(0.05, min(1.0, float(plant_treeline_ratio)))
        line_m = grid.min_elevation_m + tl * grid.elevation_range_m
        band_m = max(0.01, tl * 0.3) * grid.elevation_range_m
    else:
        line_m = float(treeline_m)
        band_m = TREELINE_TRANSITION_M
    treeline_factor = np.clip((line_m - elev.ravel()) / band_m, 0.0, 1.0)

    base_density = flat_ground_mask * treeline_factor
    if valley_boost > 0.0:
        valley_mask = analysis.static_masks["RTG_Valley"]
        base_density = np.clip(base_density + valley_mask * (float(valley_boost) * 0.35), 0.0, 1.0)

    # Multiply by treeline_factor and flat ground mask so tree line ceiling and cliff walls are strictly respected
    plant_density_mask = base_density * flat_ground_mask * (treeline_factor > 0.0)

    # Exclude negligible fringe weights below minimum cutoff
    if min_density_threshold > 0.0:
        plant_density_mask = np.where(plant_density_mask < float(min_density_threshold), 0.0, plant_density_mask)

    snow_mask = compute_structural_snow_mask(
        elev, analysis.slope_deg, analysis.curvature,
        snow_line_ratio=snow_line_ratio, snowline_m=snowline_m, shade=analysis.shade,
    )

    return {
        "RTG_Plant_Density": np.clip(plant_density_mask, 0.0, 1.0).astype(np.float32),
        "RTG_Snow": snow_mask.ravel(),
    }


def compute_botanical_weights(
    grid: ElevationGrid,
    max_plant_slope_deg: float = 35.0,
    plant_treeline_ratio: float = 0.8,
    valley_boost: float = 0.3,
    min_density_threshold: float = 0.02,
    snow_line_ratio: float = 0.65,
    treeline_m: float | None = None,
    snowline_m: float | None = None,
    analysis: TerrainAnalysis | None = None,
) -> Dict[str, np.ndarray]:
    """Compute comprehensive topographic, hydrological, and geomorphological masks from an ElevationGrid."""
    if analysis is None:
        analysis = analyze_terrain(grid)
    weights = dict(analysis.static_masks)
    weights.update(compute_dynamic_masks(
        grid,
        analysis,
        max_plant_slope_deg=max_plant_slope_deg,
        plant_treeline_ratio=plant_treeline_ratio,
        valley_boost=valley_boost,
        min_density_threshold=min_density_threshold,
        snow_line_ratio=snow_line_ratio,
        treeline_m=treeline_m,
        snowline_m=snowline_m,
    ))
    return weights


def build_terrain_mesh_data(
    grid: ElevationGrid,
    vertical_scale: float = 1.0,
    horizontal_scale: float = 1.0,
    center_origin: bool = True,
    base_elevation_zero: bool = True,
    max_plant_slope_deg: float = 35.0,
    plant_treeline_ratio: float = 0.8,
    valley_boost: float = 0.3,
    min_density_threshold: float = 0.02,
    snow_line_ratio: float = 0.65,
    treeline_m: float | None = None,
    snowline_m: float | None = None,
    analysis: TerrainAnalysis | None = None,
    compute_masks: bool = True,
) -> TerrainMeshData:
    """Generate 3D vertex positions, quad faces, UVs, and botanical weight masks from an ElevationGrid.
    
    Args:
        grid: ElevationGrid instance.
        vertical_scale: Vertical exaggeration multiplier (default 1.0 = real scale).
        horizontal_scale: Global horizontal scaling factor (default 1.0 = real meters).
        center_origin: If True, centers the mesh at X=0, Y=0.
        base_elevation_zero: If True, subtracts minimum elevation so base is at Z=0.
        max_plant_slope_deg: Maximum slope angle for vegetation (cliffs above this have 0 biomass).
        plant_treeline_ratio: Relative altitude threshold (0.0=base, 1.0=peak) above which tree growth stops.
        valley_boost: Biomass accumulation weight in protected valleys and depressions.
        min_density_threshold: Minimum biomass cutoff below which vertices receive weight 0.
        snow_line_ratio: Relative altitude above which snow accumulates.
        treeline_m / snowline_m: Absolute altitudes in meters; override the ratios when given.
        analysis: Pre-computed :func:`analyze_terrain` result to reuse.
        compute_masks: If False, ``vertex_weights`` is left empty (skips all mask analysis).
    """
    elev = grid.elevation
    rows, cols = elev.shape

    vertices = compute_scaled_vertices(
        grid=grid,
        vertical_scale=vertical_scale,
        horizontal_scale=horizontal_scale,
        center_origin=center_origin,
        base_elevation_zero=base_elevation_zero,
    )

    # UV coordinates
    u_coords = np.linspace(0.0, 1.0, cols, dtype=np.float32)
    v_coords = np.linspace(1.0, 0.0, rows, dtype=np.float32)
    uu, vv = np.meshgrid(u_coords, v_coords)
    uvs = np.column_stack([uu.ravel(), vv.ravel()]).astype(np.float32)

    # Quad faces
    r_idx = np.arange(rows - 1)[:, np.newaxis]
    c_idx = np.arange(cols - 1)[np.newaxis, :]
    v0 = (r_idx * cols + c_idx).ravel()
    v1 = ((r_idx + 1) * cols + c_idx).ravel()
    v2 = ((r_idx + 1) * cols + (c_idx + 1)).ravel()
    v3 = (r_idx * cols + (c_idx + 1)).ravel()
    faces = np.column_stack([v0, v1, v2, v3]).astype(np.int32)

    # Topographic geomorphological and botanical weight masks
    vertex_weights = {} if not compute_masks else compute_botanical_weights(
        grid=grid,
        max_plant_slope_deg=max_plant_slope_deg,
        plant_treeline_ratio=plant_treeline_ratio,
        valley_boost=valley_boost,
        min_density_threshold=min_density_threshold,
        snow_line_ratio=snow_line_ratio,
        treeline_m=treeline_m,
        snowline_m=snowline_m,
        analysis=analysis,
    )

    mesh_w = grid.width_m * float(horizontal_scale)
    mesh_h = grid.height_m * float(horizontal_scale)

    return TerrainMeshData(
        vertices=vertices,
        faces=faces,
        uvs=uvs,
        vertex_weights=vertex_weights,
        width_m=mesh_w,
        height_m=mesh_h,
        elevation_min_m=grid.min_elevation_m,
        elevation_max_m=grid.max_elevation_m,
        vertical_relief_m=grid.elevation_range_m,
    )
