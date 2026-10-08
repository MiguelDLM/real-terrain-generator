"""Realistic scene around an RTG terrain: physical sky, aerial perspective, camera and render settings.

* Sky: Blender's physical sky (multiple scattering in 5.0+, Nishita before). Its sun disc
  lights the scene, so no sun lamp is needed. The sun is placed by compass azimuth
  (0 = north, 90 = east, matching the terrain's +Y north / +X east axes) and elevation,
  and the sky is computed at the terrain's real altitude.
* Aerial perspective: a box of homogeneous haze around the terrain. Its density comes from
  the meteorological visibility (Koschmieder) converted to scene units, so distant ridges
  fade and turn blue at the right real distance whatever the terrain scale.
* Camera: an oblique landscape view from near one edge of the terrain, looking along its
  long axis with the sun to the side (side light shows relief; front light flattens it).
* Render: sample count, denoising and AgX contrast presets.
"""

from __future__ import annotations

import math

try:
    import bpy
    from mathutils import Vector
    BLENDER_AVAILABLE = True
except ImportError:
    BLENDER_AVAILABLE = False

from ..core.geo import haze_extinction_per_m

WORLD_NAME = "RTG Sky"
SKY_NODE = "RTG_Sky"
CAMERA_NAME = "RTG_Camera"
ATMOSPHERE_PREFIX = "RTG_Atmosphere_"
# Scattering albedo of the haze: mostly aerosols (near white) with some Rayleigh blue
HAZE_COLOR = (0.80, 0.88, 1.0, 1.0)
HAZE_ANISOTROPY = 0.4                    # Forward scattering: glow towards the sun
# Sky models from newest to oldest Blender
SKY_TYPES = ("MULTIPLE_SCATTERING", "NISHITA")
# The physical sky is in real radiance units (sunlight ~100000 lux); scale it to the
# usual exposure range instead of asking for a -5 EV camera exposure
SKY_STRENGTH = 0.05
CAMERA_LENS_MM = 28.0
# Minimum downward tilt: keeps the terrain filling the frame instead of the horizon past its edge
CAMERA_MIN_TILT_DEG = 12.0
# Angles between the view direction and the sun tried for the camera (side light)
SIDE_LIGHT_OFFSETS = (-110.0, -90.0, -70.0, 70.0, 90.0, 110.0)
RENDER_PRESETS = {
    # quality: (samples, adaptive noise threshold)
    "PREVIEW": (128, 0.05),
    "FINAL": (512, 0.01),
}
AGX_LOOK = "AgX - Medium High Contrast"


# --- Sky ----------------------------------------------------------------------

def get_sky_node(scene):
    world = scene.world
    if world is None or not world.get("rtg_sky") or world.node_tree is None:
        return None
    return world.node_tree.nodes.get(SKY_NODE)


def ensure_sky(scene):
    """Give the scene the RTG physical sky world (the previous world datablock is kept)."""
    world = bpy.data.worlds.get(WORLD_NAME) or bpy.data.worlds.new(WORLD_NAME)
    world["rtg_sky"] = True
    if hasattr(world, "use_nodes"):
        world.use_nodes = True
    tree = world.node_tree
    sky = tree.nodes.get(SKY_NODE)
    if sky is None:
        tree.nodes.clear()
        sky = tree.nodes.new("ShaderNodeTexSky")
        sky.name = SKY_NODE
        available = {item.identifier for item in sky.bl_rna.properties["sky_type"].enum_items}
        sky.sky_type = next(t for t in SKY_TYPES if t in available)
        sky.sun_disc = True
        background = tree.nodes.new("ShaderNodeBackground")
        output = tree.nodes.new("ShaderNodeOutputWorld")
        sky.location, background.location, output.location = (-300.0, 0.0), (0.0, 0.0), (250.0, 0.0)
        background.inputs["Strength"].default_value = SKY_STRENGTH
        tree.links.new(sky.outputs[0], background.inputs["Color"])
        tree.links.new(background.outputs[0], output.inputs["Surface"])
    if scene.world is not None and scene.world != world:
        scene.world.use_fake_user = True      # Keep the previous world to switch back to
    scene.world = world
    return sky


def update_sky(scene, elevation_deg: float, azimuth_deg: float, aerosols: float, altitude_m: float | None = None):
    """Move the sun and set the haze of the RTG sky (no-op if the scene uses another world)."""
    sky = get_sky_node(scene)
    if sky is None:
        return
    sky.sun_elevation = math.radians(elevation_deg)
    sky.sun_rotation = math.radians(azimuth_deg % 360.0)
    sky.aerosol_density = aerosols
    if altitude_m is not None:
        sky.altitude = min(max(altitude_m, 0.0), 60000.0)


def hide_sun_lamps(scene) -> int:
    """Hide the scene's sun lamps: the sky already has a sun, and two suns double the light."""
    count = 0
    for obj in scene.objects:
        if obj.type == 'LIGHT' and obj.data.type == 'SUN' and not obj.hide_render:
            obj.hide_render = True
            obj.hide_viewport = True
            count += 1
    return count


# --- Aerial perspective -------------------------------------------------------

def find_atmosphere(terrain):
    return next((c for c in terrain.children if c.get("rtg_is_atmosphere")), None)


def _world_bounds(obj):
    corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    lo = Vector((min(c.x for c in corners), min(c.y for c in corners), min(c.z for c in corners)))
    hi = Vector((max(c.x for c in corners), max(c.y for c in corners), max(c.z for c in corners)))
    return lo, hi


def _box_mesh(name: str):
    verts = [(x, y, z) for x in (-1.0, 1.0) for y in (-1.0, 1.0) for z in (-1.0, 1.0)]
    faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    return mesh


def _haze_material(name: str):
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
        if hasattr(mat, "use_nodes"):
            mat.use_nodes = True
        tree = mat.node_tree
        tree.nodes.clear()
        volume = tree.nodes.new("ShaderNodeVolumePrincipled")
        volume.inputs["Color"].default_value = HAZE_COLOR
        volume.inputs["Anisotropy"].default_value = HAZE_ANISOTROPY
        output = tree.nodes.new("ShaderNodeOutputMaterial")
        output.location = (300.0, 0.0)
        tree.links.new(volume.outputs[0], output.inputs["Volume"])
    return mat


def set_haze_visibility(atmosphere, visibility_km: float, bu_per_m: float):
    """Haze density (per scene unit) giving this visibility in real kilometers."""
    mat = atmosphere.active_material
    volume = next((n for n in mat.node_tree.nodes if n.bl_idname == "ShaderNodeVolumePrincipled"), None)
    if volume is not None:
        volume.inputs["Density"].default_value = haze_extinction_per_m(visibility_km) / max(bu_per_m, 1e-9)


def ensure_atmosphere(scene, terrain, visibility_km: float, bu_per_m: float, top_z: float):
    """Haze box covering the terrain (and the camera, up to ``top_z``)."""
    atmosphere = find_atmosphere(terrain)
    name = ATMOSPHERE_PREFIX + terrain.name
    if atmosphere is None:
        atmosphere = bpy.data.objects.new(name, _box_mesh(name))
        for coll in terrain.users_collection:
            coll.objects.link(atmosphere)
        atmosphere["rtg_is_atmosphere"] = True
        atmosphere.display_type = 'BOUNDS'
        atmosphere.hide_select = True
        atmosphere.data.materials.append(_haze_material(name))
    lo, hi = _world_bounds(terrain)
    relief = max(hi.z - lo.z, 1e-3)
    center = (lo + hi) / 2.0
    bottom = lo.z - 0.05 * relief
    top = max(hi.z, top_z) + relief
    atmosphere.parent = None
    atmosphere.location = (center.x, center.y, (bottom + top) / 2.0)
    atmosphere.rotation_euler = (0.0, 0.0, 0.0)
    # Wider than the terrain so haze continues past its edges towards the horizon
    atmosphere.scale = ((hi.x - lo.x) * 0.75, (hi.y - lo.y) * 0.75, (top - bottom) / 2.0)
    atmosphere.parent = terrain
    atmosphere.matrix_parent_inverse = terrain.matrix_world.inverted()
    set_haze_visibility(atmosphere, visibility_km, bu_per_m)

    # EEVEE only renders volumes up to this distance from the camera
    span = max(hi.x - lo.x, hi.y - lo.y)
    if hasattr(scene, "eevee") and scene.eevee.volumetric_end < span * 3.0:
        scene.eevee.volumetric_end = span * 3.0
    return atmosphere


def remove_atmosphere(terrain):
    atmosphere = find_atmosphere(terrain)
    if atmosphere is not None:
        mesh = atmosphere.data
        bpy.data.objects.remove(atmosphere)
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)


# --- Camera -------------------------------------------------------------------

def _ground_z(scene, depsgraph, x: float, y: float, start_z: float, fallback: float, ignore=()):
    origin = Vector((x, y, start_z))
    for _ in range(4):        # Step through ignored objects (haze box, rocks)
        hit, location, _n, _i, obj, _m = scene.ray_cast(depsgraph, origin, Vector((0.0, 0.0, -1.0)))
        if not hit:
            return fallback
        if obj is None or obj.original not in ignore:
            return location.z
        origin = location - Vector((0.0, 0.0, 1e-4))
    return fallback


def _edge_distance(half_x: float, half_y: float, dx: float, dy: float) -> float:
    """Distance from the center to the box edge along unit direction (dx, dy)."""
    return min(half_x / max(abs(dx), 1e-6), half_y / max(abs(dy), 1e-6))


def place_camera(context, terrain, sun_azimuth_deg: float):
    """Oblique aerial camera near one edge, looking along the terrain's long axis, sun to the side."""
    scene = context.scene
    lo, hi = _world_bounds(terrain)
    center = (lo + hi) / 2.0
    half_x, half_y = (hi.x - lo.x) / 2.0, (hi.y - lo.y) / 2.0
    relief = max(hi.z - lo.z, 1e-3)
    span = max(2.0 * half_x, 2.0 * half_y)
    long_axis = 0.0 if half_y >= half_x else 90.0

    def alignment(offset):
        view_az = math.radians(sun_azimuth_deg + offset - long_axis)
        return abs(math.cos(view_az))

    view_az = math.radians(sun_azimuth_deg + max(SIDE_LIGHT_OFFSETS, key=alignment))
    # Compass azimuth -> scene direction (+X east, +Y north)
    view = Vector((math.sin(view_az), math.cos(view_az), 0.0))
    reach = _edge_distance(half_x, half_y, view.x, view.y)

    ignore = {o for o in terrain.children if not o.get("rtg_is_water")}
    depsgraph = context.evaluated_depsgraph_get()
    start_z = hi.z + relief
    cam_xy = center - view * reach * 0.85
    # Aerial vantage above the highest point: looks down into valleys, never into a nearby wall
    cam_pos = Vector((cam_xy.x, cam_xy.y, hi.z + 0.1 * relief))
    target = Vector((center.x, center.y, _ground_z(scene, depsgraph, center.x, center.y, start_z, center.z, ignore)))
    look = (target - cam_pos).normalized()
    min_drop = math.sin(math.radians(CAMERA_MIN_TILT_DEG))
    if look.z > -min_drop:
        horizontal = Vector((look.x, look.y, 0.0)).normalized() * math.cos(math.radians(CAMERA_MIN_TILT_DEG))
        look = Vector((horizontal.x, horizontal.y, -min_drop))

    cam_obj = bpy.data.objects.get(CAMERA_NAME)
    if cam_obj is None or cam_obj.type != 'CAMERA':
        cam_obj = bpy.data.objects.new(CAMERA_NAME, bpy.data.cameras.new(CAMERA_NAME))
        scene.collection.objects.link(cam_obj)
    elif cam_obj.name not in scene.objects:
        scene.collection.objects.link(cam_obj)
    cam = cam_obj.data
    cam.lens = CAMERA_LENS_MM
    cam.clip_start = max(1e-3, span * 5e-4)
    cam.clip_end = max(cam.clip_end, span * 4.0)
    # Physical depth of field at a scaled-down terrain looks like a miniature; leave it to the user
    cam.dof.use_dof = False
    cam_obj.location = cam_pos
    cam_obj.rotation_euler = look.to_track_quat('-Z', 'Y').to_euler()
    scene.camera = cam_obj
    return cam_obj


# --- Render -------------------------------------------------------------------

def apply_render_preset(scene, quality: str):
    """Cycles samples / denoising and AgX contrast. Other engines keep their sampling."""
    if scene.render.engine == 'CYCLES':
        samples, threshold = RENDER_PRESETS[quality]
        cycles = scene.cycles
        cycles.samples = samples
        cycles.use_adaptive_sampling = True
        cycles.adaptive_threshold = threshold
        cycles.use_denoising = True
    view = scene.view_settings
    try:
        view.view_transform = 'AgX'
        view.look = AGX_LOOK
    except TypeError:      # Older Blender without AgX: keep the current transform
        pass
    view.exposure = 0.0
    view.gamma = 1.0
