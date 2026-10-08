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
Ripples are noise stretched along the flow direction and advected downstream (a frame driver
animates them), at physical size in meters, with a sharpness control for the crests.
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
WATER_GROUP_VERSION = 2
WATER_MATERIAL = "RTG_Water"
WATER_NODE = "RTG_Water"
WATER_BU_NODE = "RTG_BU_per_m"
WATER_VOLUME_NODE = "RTG_Water_Volume"
WATER_PREFIX = "RTG_Water_"

# (name, default, min, max, description); colors are handled separately
WATER_CONTROLS = (
    ("Clarity (m)", 1.5, 0.05, 30.0, "Depth at which the bed fades out (silty rivers 0.5-2 m, clear lakes 5-20 m)"),
    ("Flow Direction", 0.0, 0.0, 360.0, "Downstream direction in degrees clockwise from north"),
    ("Flow Speed (m/s)", 0.3, 0.0, 5.0, "Ripple drift speed (0 for still water)"),
    ("Ripple Size (m)", 1.5, 0.05, 50.0, "Wavelength of the surface ripples"),
    ("Ripple Strength", 0.5, 0.0, 3.0, "Height of the ripples (reflection distortion)"),
    ("Ripple Sharpness", 0.3, 0.0, 0.9, "Narrower, sharper crests (higher) or soft swell (lower)"),
    ("Shore Foam", 0.3, 0.0, 1.0, "Broken foam line along the shore"),
)
WATER_COLORS = (
    ("Shallow Color", (0.10, 0.11, 0.07, 1.0)),
    ("Deep Color", (0.008, 0.022, 0.016, 1.0)),
)
# Set by code: 0 = Shader depth mode, 1 = Volume depth mode
WATER_HIDDEN = ("Volume Mode", "Time (s)")


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
    pos = g.socket(g.attr("RTG_Position_m").outputs, "Vector")
    xyz = g.new("ShaderNodeSeparateXYZ")
    g.links.new(pos, xyz.inputs[0])
    px, py = xyz.outputs[0], xyz.outputs[1]
    g.column()

    # Shader mode: absorption along depth, 1 at the surface -> 0 in deep water
    light = g.math('EXPONENT', g.math('DIVIDE', g.mul(depth, -1.0), i["Clarity (m)"]))
    color = g.mix(light, i["Deep Color"], i["Shallow Color"])
    transmission = g.mul(light, 0.85)
    # Volume mode: a clear surface, the volume does the coloring
    color = g.mix(i["Volume Mode"], color, (1.0, 1.0, 1.0, 1.0))
    transmission = g.mixf(i["Volume Mode"], transmission, 1.0)

    # Flow frame: u downstream (advected), v across
    az = g.math('RADIANS', i["Flow Direction"])
    sin_a, cos_a = g.math('SINE', az), g.math('COSINE', az)
    u = g.add(g.mul(px, sin_a), g.mul(py, cos_a))
    v = g.math('SUBTRACT', g.mul(px, cos_a), g.mul(py, sin_a))
    u = g.math('SUBTRACT', u, g.mul(i["Time (s)"], i["Flow Speed (m/s)"]))
    inv = g.math('DIVIDE', 1.0, i["Ripple Size (m)"])
    vec = g.new("ShaderNodeCombineXYZ")
    g.feed(vec.inputs[0], g.mul(u, g.mul(inv, 0.35)))          # Stretched along the flow
    g.feed(vec.inputs[1], g.mul(v, inv))
    g.feed(vec.inputs[2], g.mul(i["Time (s)"], 0.15))         # Slow evolution in place
    g.column()
    ripples = g.noise(vec.outputs[0], 1.0, detail=6.0, roughness=0.6)
    ripples = g.smooth(ripples, g.mul(i["Ripple Sharpness"], 0.5), 1.0)   # Raise the black point: sharper crests
    fine = g.noise(vec.outputs[0], 6.0, detail=3.0, roughness=0.5)
    height = g.mul(g.add(g.add(ripples, -0.5), g.mul(g.add(fine, -0.5), 0.3)),
                   g.mul(g.mul(i["Ripple Size (m)"], 0.04), i["Ripple Strength"]))

    # Shore foam: shallowest water, broken by noise
    shore = g.smooth(depth, 0.35, 0.0)
    foam = g.mul(g.mul(shore, g.smooth(fine, 0.45, 0.7)), i["Shore Foam"])
    color = g.mix(foam, color, (0.75, 0.76, 0.72, 1.0))
    rough = g.add(0.02, g.mul(foam, 0.6))
    transmission = g.mul(transmission, g.math('SUBTRACT', 1.0, foam))
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


def _build_water_material(mat, fps: float):
    if hasattr(mat, "use_nodes"):
        mat.use_nodes = True
    tree = mat.node_tree
    tree.nodes.clear()
    g = _Graph(tree)
    time = g.new("ShaderNodeValue", label="Time (s)")
    fcurve = time.outputs[0].driver_add("default_value")
    fcurve.driver.type = 'SCRIPTED'
    fcurve.driver.expression = f"frame / {float(fps)}"
    bu = g.new("ShaderNodeValue", label="Blender units per meter")
    bu.name = WATER_BU_NODE
    group = g.new("ShaderNodeGroup", label="Water Surface")
    group.name = WATER_NODE
    group.node_tree = get_water_group()
    g.links.new(time.outputs[0], group.inputs["Time (s)"])
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


def get_water_material(fps: float):
    mat = bpy.data.materials.get(WATER_MATERIAL)
    node = mat.node_tree.nodes.get(WATER_NODE) if mat and mat.node_tree else None
    if node is not None and node.node_tree is not None and node.node_tree.get("rtg_version", 0) >= WATER_GROUP_VERSION \
            and mat.node_tree.nodes.get(WATER_VOLUME_NODE):
        return mat
    values = {s.name: s.default_value[:] if hasattr(s.default_value, "__len__") else s.default_value
              for s in node.inputs if s.name not in WATER_HIDDEN} if node else {}
    mat = mat or bpy.data.materials.new(WATER_MATERIAL)
    _build_water_material(mat, fps)
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


def ensure_water_object(terrain, fps: float):
    water = find_water(terrain)
    mat = get_water_material(fps)
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
