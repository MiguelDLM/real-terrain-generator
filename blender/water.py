"""Water surface object and material (EEVEE and Cycles).

The water mesh covers only the submerged part of the terrain (see core.water) and carries
``RTG_Water_Depth`` and ``RTG_Position_m``. Two ways to color water by depth:

* Shader (default; EEVEE and Cycles): the surface itself darkens and turns opaque with
  ``RTG_Water_Depth`` (Beer-Lambert absorption), so shallows show the bed.
* Volume (Cycles): the mesh is closed under the terrain and filled with a Principled Volume
  whose density is set in real meters (1 / Clarity), so absorption and the milky green
  scattering of silty rivers follow the true light path. The surface becomes clear glass.

In both modes shadow rays pass through the surface (Light Path "Is Shadow Ray" mixed with
a Transparent BSDF): the bed stays lit without caustic noise, and renders converge faster.

Flowing water (animatable, seamless loop):

* Rivers carry per-vertex velocity, stream coordinates (s along the flow, n across),
  whitewater and foam from core.river. Ripples and foam streaks are textured in (s, n), so
  they follow the bends, stretched along the current.
* Flow map with two phases (Vlachos 2010): each layer is advected by the local velocity for
  one period, then fades out while the other layer (half a period apart) is visible, so a
  current that varies along the river never stretches the texture over time.
* Seamless loop: the period divides the scene's frame range, and the slow evolution of the
  pattern moves on a circle in 4D noise space once per loop. Frame range and fps are read by
  drivers, so the loop follows changes to the scene.
* Rapids: whitewater coverage from the Froude number, stronger and choppier ripples, foam
  streaks drifting downstream, wind gusts that roughen patches of still water.
"""

from __future__ import annotations
import math

try:
    import bpy
    BLENDER_AVAILABLE = True
except ImportError:
    BLENDER_AVAILABLE = False

import numpy as np

from ..core.water import WaterMesh
from .materials import _Graph

WATER_GROUP = "RTG Water Surface"
WATER_GROUP_VERSION = 3
WATER_MATERIAL = "RTG_Water"
WATER_NODE = "RTG_Water"
WATER_BU_NODE = "RTG_BU_per_m"
WATER_VOLUME_NODE = "RTG_Water_Volume"
WATER_PREFIX = "RTG_Water_"
FLOW_PERIOD_S = 2.0            # Target flow-map period; adjusted to divide the loop exactly
EVOLUTION_RATE = 0.15          # Noise-space units per second of in-place pattern change
FOAM_COLOR = (0.80, 0.82, 0.80, 1.0)

# (name, default, min, max, description); colors are handled separately
WATER_CONTROLS = (
    ("Clarity (m)", 1.5, 0.05, 30.0, "Depth at which the bed fades out (silty rivers 0.5-2 m, clear lakes 5-20 m)"),
    ("Current Speed", 1.0, 0.0, 3.0, "Multiplier of the river velocity computed from the terrain (Manning)"),
    ("Flow Direction", 0.0, 0.0, 360.0, "Drift direction on still water (lakes, reservoirs), degrees clockwise from north"),
    ("Lake Drift (m/s)", 0.1, 0.0, 2.0, "Ripple drift on still water (wind, slow current)"),
    ("Ripple Size (m)", 1.5, 0.05, 50.0, "Wavelength of the surface ripples"),
    ("Ripple Strength", 0.5, 0.0, 3.0, "Height of the ripples (reflection distortion)"),
    ("Ripple Sharpness", 0.3, 0.0, 0.9, "Narrower, sharper crests (higher) or soft swell (lower)"),
    ("Whitewater", 1.0, 0.0, 2.0, "Foam and churning water on rapids and cascades (Froude number above 1)"),
    ("Foam Streaks", 0.6, 0.0, 1.0, "Foam lines drifting downstream of the rapids"),
    ("Shore Foam", 0.3, 0.0, 1.0, "Broken foam line along the shore"),
    ("Wind Gusts", 0.5, 0.0, 1.0, "Drifting patches of rougher water (cat's paws)"),
)
WATER_COLORS = (
    ("Shallow Color", (0.10, 0.11, 0.07, 1.0)),
    ("Deep Color", (0.008, 0.022, 0.016, 1.0)),
)
# Set by code: 0 = Shader depth mode, 1 = Volume depth mode; scene time and loop length (drivers)
WATER_HIDDEN = ("Volume Mode", "Time (s)", "Loop (s)")


def _noise4(g, vec, w, scale, detail, roughness=0.55):
    node = g.new("ShaderNodeTexNoise", noise_dimensions='4D', noise_type='FBM', normalize=True)
    g.feed(node.inputs["Vector"], vec)
    g.feed(node.inputs["W"], w)
    g.feed(node.inputs["Scale"], scale)
    node.inputs["Detail"].default_value = detail
    node.inputs["Roughness"].default_value = roughness
    return node.outputs[0]


def _vec(g, x, y, z):
    node = g.new("ShaderNodeCombineXYZ")
    for k, v in enumerate((x, y, z)):
        g.feed(node.inputs[k], v)
    return node.outputs[0]


def _xyz(g, vector):
    node = g.new("ShaderNodeSeparateXYZ")
    g.links.new(vector, node.inputs[0])
    return node.outputs[0], node.outputs[1], node.outputs[2]


def _build_water_group():
    group = bpy.data.node_groups.new(WATER_GROUP, 'ShaderNodeTree')
    group["rtg_version"] = WATER_GROUP_VERSION
    iface = group.interface
    for name, default, lo, hi, desc in WATER_CONTROLS:
        item = iface.new_socket(name, description=desc, in_out='INPUT', socket_type='NodeSocketFloat')
        item.default_value, item.min_value, item.max_value = default, lo, hi
    for name, default in WATER_COLORS:
        iface.new_socket(name, in_out='INPUT', socket_type='NodeSocketColor').default_value = default
    for name in WATER_HIDDEN:
        iface.new_socket(name, in_out='INPUT', socket_type='NodeSocketFloat')
    for name, kind in (("Color", 'NodeSocketColor'), ("Roughness", 'NodeSocketFloat'),
                       ("Transmission", 'NodeSocketFloat'), ("Height (m)", 'NodeSocketFloat'),
                       ("Density per m", 'NodeSocketFloat'), ("Scatter Color", 'NodeSocketColor'),
                       ("Absorption Color", 'NodeSocketColor')):
        iface.new_socket(name, in_out='OUTPUT', socket_type=kind)

    g = _Graph(group)
    gi = g.new("NodeGroupInput")
    i = {s.name: s for s in gi.outputs}
    depth = g.math('MAXIMUM', g.attr_fac("RTG_Water_Depth"), 0.0)
    px, py, _ = _xyz(g, g.socket(g.attr("RTG_Position_m").outputs, "Vector"))
    s, n, river_w = _xyz(g, g.socket(g.attr("RTG_Water_Flow_Coords").outputs, "Vector"))
    vel = g.socket(g.attr("RTG_Water_Velocity").outputs, "Vector")
    vx, vy, _ = _xyz(g, vel)
    white = g.mul(g.attr_fac("RTG_Water_Whitewater"), i["Whitewater"])
    foam_carried = g.attr_fac("RTG_Water_Foam")
    g.column()

    # Shader mode: absorption along depth, 1 at the surface -> 0 in deep water
    light = g.math('EXPONENT', g.math('DIVIDE', g.mul(depth, -1.0), i["Clarity (m)"]))
    color = g.mix(light, i["Deep Color"], i["Shallow Color"])
    transmission = g.mul(light, 0.85)
    # Volume mode: a clear surface, the volume does the coloring
    color = g.mix(i["Volume Mode"], color, (1.0, 1.0, 1.0, 1.0))
    transmission = g.mixf(i["Volume Mode"], transmission, 1.0)

    # --- Time: flow-map phases and periodic evolution ----------------------------
    t = i["Time (s)"]
    loop = g.math('MAXIMUM', i["Loop (s)"], 0.1)
    period = g.math('DIVIDE', loop, g.math('MAXIMUM', g.math('ROUND', g.math('DIVIDE', loop, FLOW_PERIOD_S)), 1.0))
    phase0 = g.math('FRACT', g.math('DIVIDE', t, period))
    phase1 = g.math('FRACT', g.add(phase0, 0.5))
    # Layer 0 is invisible when it jumps back (phase 0) and fully visible half a period later
    w0 = g.math('SUBTRACT', 1.0, g.math('ABSOLUTE', g.math('SUBTRACT', 1.0, g.mul(phase0, 2.0))))
    # Two blended layers have less contrast; rescale around the mean
    contrast = g.math('INVERSE_SQRT', g.add(g.mul(w0, w0), g.mul(g.math('SUBTRACT', 1.0, w0), g.math('SUBTRACT', 1.0, w0))))
    angle = g.mul(g.math('DIVIDE', t, loop), 2.0 * math.pi)
    radius = g.mul(loop, EVOLUTION_RATE / (2.0 * math.pi))
    ez = g.mul(g.math('COSINE', angle), radius)
    ew = g.mul(g.math('SINE', angle), radius)
    g.column()

    # Still water frame: u along the drift direction, v across
    az = g.math('RADIANS', i["Flow Direction"])
    sin_a, cos_a = g.math('SINE', az), g.math('COSINE', az)
    u_lake = g.add(g.mul(px, sin_a), g.mul(py, cos_a))
    v_lake = g.math('SUBTRACT', g.mul(px, cos_a), g.mul(py, sin_a))
    length = g.new("ShaderNodeVectorMath", operation='LENGTH')
    g.links.new(vel, length.inputs[0])
    speed = g.mul(g.socket(length.outputs, "Value"), i["Current Speed"])
    still = g.math('SUBTRACT', 1.0, river_w)
    drift = g.mul(i["Lake Drift (m/s)"], still)
    # World velocity for the isotropic fine detail
    wvx = g.add(g.mul(vx, i["Current Speed"]), g.mul(drift, sin_a))
    wvy = g.add(g.mul(vy, i["Current Speed"]), g.mul(drift, cos_a))
    inv = g.math('DIVIDE', 1.0, i["Ripple Size (m)"])

    def layer(phase, offset):
        travel = g.mul(phase, period)
        a_river = g.add(g.math('SUBTRACT', s, g.mul(speed, travel)), offset)
        b_river = g.add(n, offset * 0.37)
        a_lake = g.add(g.math('SUBTRACT', u_lake, g.mul(i["Lake Drift (m/s)"], travel)), offset)
        b_lake = g.add(v_lake, offset * 0.37)
        ripple_river = _noise4(g, _vec(g, g.mul(a_river, g.mul(inv, 0.35)), g.mul(b_river, inv), ez), ew, 1.0, 4.0, 0.6)
        ripple_lake = _noise4(g, _vec(g, g.mul(a_lake, g.mul(inv, 0.35)), g.mul(b_lake, inv), ez), ew, 1.0, 4.0, 0.6)
        fx = g.add(g.math('SUBTRACT', px, g.mul(wvx, travel)), offset)
        fy = g.math('SUBTRACT', py, g.mul(wvy, travel))
        fine = _noise4(g, _vec(g, g.mul(fx, inv), g.mul(fy, inv), ez), ew, 6.0, 2.0, 0.5)
        streak = _noise4(g, _vec(g, g.mul(a_river, 0.08), g.mul(b_river, 0.6), ez), ew, 1.0, 3.0, 0.6)
        churn = _noise4(g, _vec(g, g.mul(a_river, 0.5), g.mul(b_river, 0.5), g.mul(ez, 4.0)), g.mul(ew, 4.0), 1.0, 3.0, 0.6)
        # Boils and eddies: patches tens of meters long, smooth or rippled, drifting with the current
        boil = _noise4(g, _vec(g, g.mul(a_river, 0.03), g.mul(b_river, 0.08), g.mul(ez, 2.0)), g.mul(ew, 2.0), 1.0, 2.0, 0.5)
        g.column()
        return ripple_river, ripple_lake, fine, streak, churn, boil

    l0 = layer(phase0, 0.0)
    l1 = layer(phase1, 17.3)

    def blend(k):
        mixed = g.mixf(w0, l1[k], l0[k])
        return g.add(g.mul(g.add(mixed, -0.5), contrast), 0.5)

    ripple_river, ripple_lake, fine, streak, churn, boil = (blend(k) for k in range(6))
    ripples = g.mixf(river_w, ripple_lake, ripple_river)
    ripples = g.smooth(ripples, g.mul(i["Ripple Sharpness"], 0.5), 1.0)   # Raise the black point: sharper crests
    g.column()

    # Wind gusts: large drifting patches of rougher water
    gust_n = _noise4(g, _vec(g, g.mul(px, 0.025), g.mul(py, 0.025), g.mul(ez, 0.5)), g.mul(ew, 0.5), 1.0, 2.0, 0.5)
    gust = g.mul(g.smooth(gust_n, 0.45, 0.7), i["Wind Gusts"])
    amplitude = g.add(g.add(1.0, g.mul(i["Wind Gusts"], -0.5)), g.mul(gust, 1.5))
    amplitude = g.mul(amplitude, g.add(1.0, g.mul(white, 3.0)))          # Choppy rapids
    # Current texture: boils smooth the surface, shear between them ripples it
    current = g.mul(river_w, g.smooth(speed, 0.1, 1.0))
    boil_f = g.smooth(boil, 0.35, 0.65)
    amplitude = g.mul(amplitude, g.mixf(current, 1.0, g.add(0.3, g.mul(boil_f, 1.4))))
    height = g.mul(g.add(g.add(ripples, -0.5), g.mul(g.add(fine, -0.5), 0.3)),
                   g.mul(g.mul(g.mul(i["Ripple Size (m)"], 0.04), i["Ripple Strength"]), amplitude))

    # Foam: shoreline, churning rapids, streaks carried downstream
    shore = g.mul(g.mul(g.smooth(depth, 0.35, 0.0), g.smooth(fine, 0.45, 0.7)), i["Shore Foam"])
    # Rapids stay patchy: about half covered at full whitewater, nearly all at twice that
    churn_cov = g.smooth(churn, g.math('SUBTRACT', 1.0, g.mul(white, 0.55)), g.math('SUBTRACT', 1.2, g.mul(white, 0.55)))
    streaks = g.mul(g.mul(g.smooth(streak, 0.6, 0.75), foam_carried),
                    g.mul(i["Foam Streaks"], g.smooth(speed, 0.0, 0.5)))
    foam = g.math('MAXIMUM', g.math('MAXIMUM', shore, churn_cov), streaks)
    color = g.mix(g.mul(white, 0.3), color, (0.45, 0.5, 0.45, 1.0))      # Aerated water is paler
    color = g.mix(foam, color, FOAM_COLOR)
    # Ripples smaller than a pixel still blur the reflections: roughness follows ripple height
    micro = g.mul(g.mul(i["Ripple Strength"], amplitude), 0.04)
    rough = g.add(g.add(g.add(0.015, micro), g.mul(gust, 0.05)), g.mul(foam, 0.6))
    transmission = g.mul(transmission, g.math('SUBTRACT', 1.0, foam))
    height = g.add(height, g.mul(churn_cov, 0.05))                        # Lumpy aerated water
    g.column()

    go = g.new("NodeGroupOutput")
    g.links.new(color, go.inputs["Color"])
    g.links.new(rough, go.inputs["Roughness"])
    g.links.new(transmission, go.inputs["Transmission"])
    g.links.new(height, go.inputs["Height (m)"])
    g.links.new(g.math('DIVIDE', 1.0, i["Clarity (m)"]), go.inputs["Density per m"])
    g.links.new(i["Shallow Color"], go.inputs["Scatter Color"])
    g.links.new(i["Deep Color"], go.inputs["Absorption Color"])
    return group


def get_water_group():
    group = bpy.data.node_groups.get(WATER_GROUP)
    if group is not None and group.get("rtg_version", 0) < WATER_GROUP_VERSION:
        bpy.data.node_groups.remove(group)
        group = None
    return group or _build_water_group()


def _scene_driver(socket, scene, expression: str):
    """Driver on ``socket`` reading the frame range and frame rate of ``scene``."""
    driver = socket.driver_add("default_value").driver
    driver.type = 'SCRIPTED'
    for name, path in (("start", "frame_start"), ("end", "frame_end"),
                       ("fps", "render.fps"), ("base", "render.fps_base")):
        var = driver.variables.new()
        var.name, var.type = name, 'SINGLE_PROP'
        var.targets[0].id_type = 'SCENE'
        var.targets[0].id = scene
        var.targets[0].data_path = path
    driver.expression = expression


def _build_water_material(mat, scene):
    if hasattr(mat, "use_nodes"):
        mat.use_nodes = True
    tree = mat.node_tree
    tree.nodes.clear()
    g = _Graph(tree)
    # Seconds since the first frame, and the length of the frame range (one seamless loop)
    time = g.new("ShaderNodeValue", label="Time (s)")
    _scene_driver(time.outputs[0], scene, "(frame - start) / (fps / base)")
    loop = g.new("ShaderNodeValue", label="Loop (s)")
    _scene_driver(loop.outputs[0], scene, "(end - start + 1) / (fps / base)")
    bu = g.new("ShaderNodeValue", label="Blender units per meter")
    bu.name = WATER_BU_NODE
    group = g.new("ShaderNodeGroup", label="Water Surface")
    group.name = WATER_NODE
    group.node_tree = get_water_group()
    g.links.new(time.outputs[0], group.inputs["Time (s)"])
    g.links.new(loop.outputs[0], group.inputs["Loop (s)"])
    g.column()

    bsdf = g.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["IOR"].default_value = 1.333
    g.links.new(group.outputs["Color"], bsdf.inputs["Base Color"])
    g.links.new(group.outputs["Roughness"], bsdf.inputs["Roughness"])
    g.links.new(group.outputs["Transmission"], g.socket(bsdf.inputs, "Transmission Weight", "Transmission"))
    # Shadow rays pass straight through: lit bed, no caustic noise, faster convergence
    light_path = g.new("ShaderNodeLightPath")
    transparent = g.new("ShaderNodeBsdfTransparent")
    mix = g.new("ShaderNodeMixShader")
    g.links.new(g.socket(light_path.outputs, "Is Shadow Ray"), mix.inputs[0])
    g.links.new(bsdf.outputs[0], mix.inputs[1])
    g.links.new(transparent.outputs[0], mix.inputs[2])

    volume = g.new("ShaderNodeVolumePrincipled")
    volume.name = WATER_VOLUME_NODE
    g.links.new(group.outputs["Scatter Color"], volume.inputs["Color"])
    g.links.new(group.outputs["Absorption Color"], _Graph.socket(volume.inputs, "Absorption Color"))
    volume.inputs["Anisotropy"].default_value = 0.8          # Forward scattering of suspended silt
    # Density per Blender unit = (1 / Clarity in m) / (units per m)
    g.links.new(g.math('DIVIDE', group.outputs["Density per m"], bu.outputs[0]), volume.inputs["Density"])

    disp = g.new("ShaderNodeDisplacement", space='WORLD')
    disp.inputs["Midlevel"].default_value = 0.0
    disp.inputs["Scale"].default_value = 1.0
    g.links.new(g.math('MULTIPLY', group.outputs["Height (m)"], bu.outputs[0]), disp.inputs["Height"])
    g.column()
    out = g.new("ShaderNodeOutputMaterial")
    g.links.new(mix.outputs[0], out.inputs["Surface"])
    g.links.new(disp.outputs[0], out.inputs["Displacement"])

    # EEVEE: screen-space refraction so the shallows show the bed; accurate volume bounds
    if hasattr(mat, "use_raytrace_refraction"):
        mat.use_raytrace_refraction = True
    if hasattr(mat, "surface_render_method"):
        mat.surface_render_method = 'DITHERED'
    if hasattr(mat, "volume_intersection_method"):
        mat.volume_intersection_method = 'ACCURATE'


def get_water_material(scene):
    mat = bpy.data.materials.get(WATER_MATERIAL)
    node = mat.node_tree.nodes.get(WATER_NODE) if mat and mat.node_tree else None
    if node is not None and node.node_tree is not None and node.node_tree.get("rtg_version", 0) >= WATER_GROUP_VERSION \
            and mat.node_tree.nodes.get(WATER_VOLUME_NODE):
        return mat
    values = {s.name: s.default_value[:] if hasattr(s.default_value, "__len__") else s.default_value
              for s in node.inputs if s.name not in WATER_HIDDEN} if node else {}
    mat = mat or bpy.data.materials.new(WATER_MATERIAL)
    _build_water_material(mat, scene)
    new_node = mat.node_tree.nodes[WATER_NODE]
    for name, value in values.items():
        if name in new_node.inputs:
            new_node.inputs[name].default_value = value
    return mat


def set_volume_mode(mat, enabled: bool):
    """Volume depth mode: connect the volume and clear the surface (Cycles); off: shader depth mode."""
    tree = mat.node_tree
    node = tree.nodes.get(WATER_NODE)
    volume = tree.nodes.get(WATER_VOLUME_NODE)
    out = next(n for n in tree.nodes if n.bl_idname == "ShaderNodeOutputMaterial")
    node.inputs["Volume Mode"].default_value = 1.0 if enabled else 0.0
    if enabled:
        tree.links.new(volume.outputs[0], out.inputs["Volume"])
    else:
        for link in list(out.inputs["Volume"].links):
            tree.links.remove(link)


def get_water_node(water_obj):
    mat = water_obj.active_material if water_obj else None
    return mat.node_tree.nodes.get(WATER_NODE) if mat and mat.node_tree else None


def find_water(terrain):
    return next((c for c in terrain.children if c.get("rtg_is_water")), None)


def write_water_mesh(water_obj, data: WaterMesh, bu_per_m: float):
    """Replace the water object's geometry with ``data`` (C-speed foreach_set)."""
    old = water_obj.data
    mesh = bpy.data.meshes.new(old.name)
    n_faces = len(data.faces)
    mesh.vertices.add(len(data.vertices))
    mesh.vertices.foreach_set("co", data.vertices.ravel())
    mesh.loops.add(n_faces * 4)
    mesh.loops.foreach_set("vertex_index", data.faces.ravel())
    mesh.polygons.add(n_faces)
    mesh.polygons.foreach_set("loop_start", np.arange(0, n_faces * 4, 4, dtype=np.int32))
    mesh.update(calc_edges=True)
    mesh.shade_smooth()
    mesh.uv_layers.new(name="UVMap").data.foreach_set("uv", data.uvs[data.faces.ravel()].ravel())
    mesh.attributes.new("RTG_Water_Depth", 'FLOAT', 'POINT').data.foreach_set("value", data.depth_m)
    mesh.attributes.new("RTG_Position_m", 'FLOAT_VECTOR', 'POINT').data.foreach_set("vector", data.position_m.ravel())
    mesh.attributes.new("RTG_Water_Velocity", 'FLOAT_VECTOR', 'POINT').data.foreach_set("vector", data.velocity.ravel())
    mesh.attributes.new("RTG_Water_Flow_Coords", 'FLOAT_VECTOR', 'POINT').data.foreach_set("vector", data.flow_coords.ravel())
    mesh.attributes.new("RTG_Water_Whitewater", 'FLOAT', 'POINT').data.foreach_set("value", data.whitewater)
    mesh.attributes.new("RTG_Water_Foam", 'FLOAT', 'POINT').data.foreach_set("value", data.foam)
    for mat in old.materials:
        mesh.materials.append(mat)
    water_obj.data = mesh
    if old.users == 0:
        bpy.data.meshes.remove(old)
    update_water_scale(water_obj, bu_per_m)


def update_water_scale(water_obj, bu_per_m: float):
    mat = water_obj.active_material
    node = mat.node_tree.nodes.get(WATER_BU_NODE) if mat and mat.node_tree else None
    if node:
        node.outputs[0].default_value = bu_per_m


def ensure_water_object(terrain, scene):
    water = find_water(terrain)
    mat = get_water_material(scene)
    if water is None:
        mesh = bpy.data.meshes.new(WATER_PREFIX + terrain.name)
        water = bpy.data.objects.new(WATER_PREFIX + terrain.name, mesh)
        for coll in terrain.users_collection:
            coll.objects.link(water)
        water.parent = terrain
        water["rtg_is_water"] = True
    if not water.data.materials:
        water.data.materials.append(mat)
    return water


def set_flow_direction(water_obj, azimuth_deg: float):
    node = get_water_node(water_obj)
    if node and azimuth_deg is not None and not math.isnan(azimuth_deg):
        node.inputs["Flow Direction"].default_value = float(azimuth_deg)
