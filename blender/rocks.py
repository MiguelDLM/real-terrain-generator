"""Loose rocks on the terrain: a procedural rock library scattered with Geometry Nodes.

* Library: angular blocks, rounded cobbles, slabs and basalt prisms (core.rocks), a few
  variants each, 1 m in size, in the "RTG Rock Library" collection (excluded from the view
  layer, only seen through instances).
* Scatter: a "RTG_Rocks_<terrain>" object (child of the terrain) carries the
  "RTG Rock Scatter" modifier. It reads the *evaluated* terrain, so rocks sit on the walls
  sculpted by the Cliff Detail modifier, and uses the terrain attributes:
  ``RTG_Boulder_Density`` (where), ``RTG_Clast_Size_m`` (how big),
  ``RTG_Clast_Rounding`` / ``RTG_Rock_Fracture`` (which shape). Rocks align to the ground,
  sink partly into it, and can be limited to a radius around the camera and thinned in the
  viewport for fluid ground-level work.
* Material: each instance inherits the color of the rock it rests on (``RTG_Rock_Color``),
  with moss on upward faces and a dark wet band at the waterline (from per-instance height
  above water, so it needs no world-space values).
"""

from __future__ import annotations

try:
    import bpy
    BLENDER_AVAILABLE = True
except ImportError:
    BLENDER_AVAILABLE = False

from ..core.rocks import ROCK_SHAPES, rock_library
from .materials import _Graph

LIBRARY = "RTG Rock Library"
SCATTER_GROUP = "RTG Rock Scatter"
SCATTER_VERSION = 1
SCATTER_MODIFIER = "RTG Rock Scatter"
ROCK_MATERIAL = "RTG_Rock"
ROCK_SURFACE_GROUP = "RTG Rock Surface"
ROCK_SURFACE_VERSION = 1
ROCK_PREFIX = "RTG_Rocks_"
VARIANTS = 4

# (name, socket type, default, min, max, description) shown in the sidebar
SCATTER_INPUTS = (
    ("Rocks per 100 m²", 'NodeSocketFloat', 3.0, 0.0, 500.0, "Rock count where the boulder density mask is 1"),
    ("Size Multiplier", 'NodeSocketFloat', 1.0, 0.05, 10.0, "Scales the clast sizes computed from the geology"),
    ("Min Size (m)", 'NodeSocketFloat', 0.25, 0.0, 10.0, "Skip rocks smaller than this (the material covers gravel)"),
    ("Embed", 'NodeSocketFloat', 0.35, 0.0, 1.0, "How deep rocks sink into the ground"),
    ("Viewport Display", 'NodeSocketFloat', 0.3, 0.0, 1.0, "Fraction of rocks shown in the viewport (renders show all)"),
    ("Camera Range (m)", 'NodeSocketFloat', 0.0, 0.0, 100000.0, "Only rocks within this distance of the camera (0 = everywhere)"),
    ("Seed", 'NodeSocketInt', 0, 0, 100000, "Variation of the rock placement"),
)
# Set by code
SCATTER_HIDDEN = (
    ("Surface", 'NodeSocketObject'), ("Library", 'NodeSocketCollection'), ("Camera", 'NodeSocketObject'),
    ("Blender Units per Meter", 'NodeSocketFloat'), ("Water Level (m)", 'NodeSocketFloat'),
)

ROCK_CONTROLS = (
    ("Moss Amount", 0.4, 0.0, 1.0, "Moss on upward-facing rock surfaces"),
    ("Color Variation", 0.5, 0.0, 1.0, "Tone variation across each rock"),
    ("Bump Strength", 0.6, 0.0, 2.0, "Fine surface relief"),
    ("Wet Band (m)", 0.3, 0.0, 5.0, "Dark, glossy band above the waterline"),
)


# --- Library -------------------------------------------------------------------
def _exclude_from_view_layer(context, collection):
    def walk(layer_coll):
        if layer_coll.collection == collection:
            layer_coll.exclude = True
            return True
        return any(walk(child) for child in layer_coll.children)
    walk(context.view_layer.layer_collection)


def get_rock_library(context):
    """The rock library collection, built once (angular, rounded, platy, columnar x VARIANTS)."""
    lib = bpy.data.collections.get(LIBRARY)
    if lib is not None and len(lib.objects) == len(ROCK_SHAPES) * VARIANTS:
        return lib
    if lib is None:
        lib = bpy.data.collections.new(LIBRARY)
        context.scene.collection.children.link(lib)
    for obj in list(lib.objects):
        bpy.data.objects.remove(obj)
    mat = get_rock_material()
    # Children are picked by index in name order: zero-padded so shape order is kept
    for i, (shape, variant, verts, faces) in enumerate(rock_library(VARIANTS)):
        name = f"RTG_Rock_{i:02d}_{shape}_{variant}"
        mesh = bpy.data.meshes.new(name)
        mesh.from_pydata(verts.tolist(), [], faces)
        mesh.update()
        if shape == "rounded":
            mesh.shade_smooth()
        mesh.materials.append(mat)
        obj = bpy.data.objects.new(name, mesh)
        lib.objects.link(obj)
    _exclude_from_view_layer(context, lib)
    return lib


# --- Material ------------------------------------------------------------------
def _build_rock_surface_group():
    group = bpy.data.node_groups.new(ROCK_SURFACE_GROUP, 'ShaderNodeTree')
    group["rtg_version"] = ROCK_SURFACE_VERSION
    iface = group.interface
    for name, default, lo, hi, desc in ROCK_CONTROLS:
        item = iface.new_socket(name, description=desc, in_out='INPUT', socket_type='NodeSocketFloat')
        item.default_value, item.min_value, item.max_value = default, lo, hi
    for name, kind in (("Color", 'NodeSocketColor'), ("Roughness", 'NodeSocketFloat'), ("Normal", 'NodeSocketVector')):
        iface.new_socket(name, in_out='OUTPUT', socket_type=kind)

    g = _Graph(group)
    gi = g.new("NodeGroupInput")
    i = {s.name: s for s in gi.outputs}

    def inst(name):
        return g.new("ShaderNodeAttribute", label=name, attribute_type='INSTANCER', attribute_name=name)

    base = g.socket(inst("RTG_Rock_Color").outputs, "Color")
    height_above_water = g.socket(inst("RTG_Height_Above_Water_m").outputs, "Fac", "Factor")
    size = g.socket(inst("RTG_Rock_Size_m").outputs, "Fac", "Factor")
    # Fallback gray when the instance has no color (e.g. rocks used outside RTG)
    rgb = g.new("ShaderNodeSeparateColor")
    g.links.new(base, rgb.inputs[0])
    has_color = g.smooth(g.add(g.add(rgb.outputs[0], rgb.outputs[1]), rgb.outputs[2]), 0.0, 0.01)
    base = g.mix(has_color, (0.35, 0.34, 0.32, 1.0), base)

    coord = g.socket(g.new("ShaderNodeTexCoord").outputs, "Object")
    rand = g.socket(g.new("ShaderNodeObjectInfo").outputs, "Random")
    p = g.new("ShaderNodeVectorMath", operation='ADD')
    g.links.new(coord, p.inputs[0])
    off = g.new("ShaderNodeCombineXYZ")
    for k in range(3):
        g.feed(off.inputs[k], g.mul(rand, 37.0 + 11.0 * k))
    g.links.new(off.outputs[0], p.inputs[1])
    p = p.outputs[0]
    g.column()

    tone = g.noise(p, 2.5, detail=5.0)
    cracks = g.voronoi(p, 3.0, 'DISTANCE_TO_EDGE')
    col = g.mix(g.mul(g.smooth(tone, 0.3, 0.7), i["Color Variation"]), g.mix(1.0, base, g.gray(0.7), blend='MULTIPLY'),
                g.mix(1.0, base, g.gray(1.15), blend='MULTIPLY'))
    col = g.mix(g.smooth(cracks, 0.03, 0.0), col, g.gray(0.45), blend='MULTIPLY')

    # Moss on top faces
    up = g.new("ShaderNodeNewGeometry")
    nz = g.new("ShaderNodeSeparateXYZ")
    g.links.new(g.socket(up.outputs, "Normal"), nz.inputs[0])
    moss_f = g.mul(g.smooth(g.add(g.smooth(nz.outputs[2], 0.35, 0.85), g.mul(g.add(tone, -0.5), 0.6)), 0.4, 0.7), i["Moss Amount"])
    col = g.mix(moss_f, col, (0.05, 0.075, 0.025, 1.0))

    # Waterline: height of the shading point above water, from the instance base and object Z
    cz = g.new("ShaderNodeSeparateXYZ")
    g.links.new(coord, cz.inputs[0])
    h = g.add(height_above_water, g.mul(cz.outputs[2], size))
    band = g.math('MAXIMUM', i["Wet Band (m)"], 0.01)
    wet = g.smooth(g.add(h, g.mul(g.add(tone, -0.5), band)), band, 0.0)
    col = g.mix(g.mul(wet, 0.55), col, (0.0, 0.0, 0.0, 1.0))
    rough = g.mixf(wet, g.mixf(moss_f, 0.75, 0.9), 0.12)
    g.column()

    bump = g.new("ShaderNodeBump")
    g.feed(bump.inputs["Strength"], g.mul(i["Bump Strength"], 0.5))
    g.feed(bump.inputs["Height"], g.add(tone, g.mul(cracks, 0.5)))
    go = g.new("NodeGroupOutput")
    g.links.new(col, go.inputs["Color"])
    g.links.new(rough, go.inputs["Roughness"])
    g.links.new(bump.outputs[0], go.inputs["Normal"])
    return group


def get_rock_material():
    mat = bpy.data.materials.get(ROCK_MATERIAL)
    node = mat.node_tree.nodes.get("RTG_Rock_Surface") if mat and mat.node_tree else None
    if node is not None and node.node_tree and node.node_tree.get("rtg_version", 0) >= ROCK_SURFACE_VERSION:
        return mat
    group = bpy.data.node_groups.get(ROCK_SURFACE_GROUP)
    if group is not None:
        bpy.data.node_groups.remove(group)
    mat = mat or bpy.data.materials.new(ROCK_MATERIAL)
    if hasattr(mat, "use_nodes"):
        mat.use_nodes = True
    tree = mat.node_tree
    tree.nodes.clear()
    g = _Graph(tree)
    surface = g.new("ShaderNodeGroup", label="Rock Surface")
    surface.name = "RTG_Rock_Surface"
    surface.node_tree = _build_rock_surface_group()
    g.column()
    bsdf = g.new("ShaderNodeBsdfPrincipled")
    g.links.new(surface.outputs["Color"], bsdf.inputs["Base Color"])
    g.links.new(surface.outputs["Roughness"], bsdf.inputs["Roughness"])
    g.links.new(surface.outputs["Normal"], bsdf.inputs["Normal"])
    g.column()
    out = g.new("ShaderNodeOutputMaterial")
    g.links.new(bsdf.outputs[0], out.inputs["Surface"])
    return mat


def get_rock_surface_node():
    mat = bpy.data.materials.get(ROCK_MATERIAL)
    return mat.node_tree.nodes.get("RTG_Rock_Surface") if mat and mat.node_tree else None


# --- Scatter -------------------------------------------------------------------
def _build_scatter_group():
    tree = bpy.data.node_groups.new(SCATTER_GROUP, 'GeometryNodeTree')
    tree["rtg_version"] = SCATTER_VERSION
    iface = tree.interface
    iface.new_socket("Geometry", in_out='INPUT', socket_type='NodeSocketGeometry')
    for name, kind, default, lo, hi, desc in SCATTER_INPUTS:
        item = iface.new_socket(name, description=desc, in_out='INPUT', socket_type=kind)
        item.default_value, item.min_value, item.max_value = default, lo, hi
    for name, kind in SCATTER_HIDDEN:
        iface.new_socket(name, in_out='INPUT', socket_type=kind)
    iface.new_socket("Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')

    from .terrain_detail import _GN
    g = _GN(tree)
    gi = g.new("NodeGroupInput")
    i = {s.name: s for s in gi.outputs}
    bu = i["Blender Units per Meter"]

    info = g.new("GeometryNodeObjectInfo", transform_space='RELATIVE')
    g.links.new(i["Surface"], g.sock(info.inputs, "Object"))
    # Density per square Blender unit = rocks per m² / (units per m)²
    per_m2 = g.math('DIVIDE', i["Rocks per 100 m²"], 100.0)
    per_bu2 = g.math('DIVIDE', per_m2, g.math('MAXIMUM', g.math('MULTIPLY', bu, bu), 1e-12))
    dist = g.new("GeometryNodeDistributePointsOnFaces", distribute_method='RANDOM')
    g.links.new(g.sock(info.outputs, "Geometry"), g.sock(dist.inputs, "Mesh"))
    g.feed(g.sock(dist.inputs, "Density"), g.math('MULTIPLY', per_bu2, g.named("RTG_Boulder_Density", 'FLOAT')))
    g.links.new(i["Seed"], g.sock(dist.inputs, "Seed"))
    points = g.sock(dist.outputs, "Points")
    normal = g.sock(dist.outputs, "Normal")

    # Size in meters, with per-rock variation
    rnd_size = g.new("FunctionNodeRandomValue", data_type='FLOAT')
    rnd_size.inputs[2].default_value = 0.6
    rnd_size.inputs[3].default_value = 1.4
    g.feed(g.sock(rnd_size.inputs, "Seed"), g.math('ADD', i["Seed"], 11.0))
    size_m = g.math('MULTIPLY', g.math('MULTIPLY', g.named("RTG_Clast_Size_m", 'FLOAT'), rnd_size.outputs[1]), i["Size Multiplier"])

    # Culling: too small, viewport thinning, beyond camera range
    too_small = g.math('LESS_THAN', size_m, i["Min Size (m)"])
    rnd_keep = g.new("FunctionNodeRandomValue", data_type='BOOLEAN')
    g.feed(g.sock(rnd_keep.inputs, "Probability"), g.math('SUBTRACT', 1.0, i["Viewport Display"]))
    g.feed(g.sock(rnd_keep.inputs, "Seed"), g.math('ADD', i["Seed"], 23.0))
    thin = g.new("FunctionNodeBooleanMath", operation='AND')
    g.links.new(g.sock(g.new("GeometryNodeIsViewport").outputs, "Is Viewport"), thin.inputs[0])
    g.links.new(rnd_keep.outputs[3], thin.inputs[1])
    cam = g.new("GeometryNodeObjectInfo", transform_space='RELATIVE')
    g.links.new(i["Camera"], g.sock(cam.inputs, "Object"))
    position = g.sock(g.new("GeometryNodeInputPosition").outputs, "Position")
    distance = g.new("ShaderNodeVectorMath", operation='DISTANCE')
    g.links.new(position, distance.inputs[0])
    g.links.new(g.sock(cam.outputs, "Location"), distance.inputs[1])
    far = g.math('MULTIPLY', g.math('GREATER_THAN', i["Camera Range (m)"], 0.0),
                 g.math('GREATER_THAN', g.sock(distance.outputs, "Value"), g.math('MULTIPLY', i["Camera Range (m)"], bu)))
    drop = g.math('MAXIMUM', g.math('MAXIMUM', too_small, far), thin.outputs[0])
    delete = g.new("GeometryNodeDeleteGeometry", domain='POINT')
    g.links.new(points, g.sock(delete.inputs, "Geometry"))
    g.feed(g.sock(delete.inputs, "Selection"), drop)
    points = delete.outputs[0]

    # Shape: rounded (1) if well rounded, columnar (3) / platy (2) by parent fracture, else angular (0)
    frac = g.split(g.named("RTG_Rock_Fracture", 'FLOAT_COLOR'))
    rounded = g.math('GREATER_THAN', g.named("RTG_Clast_Rounding", 'FLOAT'), 0.6)
    columnar = g.math('GREATER_THAN', frac[1], 0.5)
    platy = g.math('GREATER_THAN', frac[2], 0.6)
    shape = g.math('MULTIPLY', platy, 2.0)
    shape = g.math('ADD', g.math('MULTIPLY', shape, g.math('SUBTRACT', 1.0, columnar)), g.math('MULTIPLY', columnar, 3.0))
    shape = g.math('ADD', g.math('MULTIPLY', shape, g.math('SUBTRACT', 1.0, rounded)), rounded)
    rnd_var = g.new("FunctionNodeRandomValue", data_type='INT')
    rnd_var.inputs[4].default_value = 0
    rnd_var.inputs[5].default_value = VARIANTS - 1
    g.feed(g.sock(rnd_var.inputs, "Seed"), g.math('ADD', i["Seed"], 31.0))
    index = g.math('ADD', g.math('MULTIPLY', shape, float(VARIANTS)), rnd_var.outputs[2])

    # Orientation: up axis along the ground normal, random spin around it
    align = g.new("FunctionNodeAlignRotationToVector", axis='Z')
    g.links.new(normal, g.sock(align.inputs, "Vector"))
    rnd_spin = g.new("FunctionNodeRandomValue", data_type='FLOAT')
    rnd_spin.inputs[2].default_value = 0.0
    rnd_spin.inputs[3].default_value = 6.2831853
    g.feed(g.sock(rnd_spin.inputs, "Seed"), g.math('ADD', i["Seed"], 47.0))
    euler = g.new("FunctionNodeEulerToRotation")
    g.links.new(g.xyz(0.0, 0.0, rnd_spin.outputs[1]), euler.inputs[0])
    rotate = g.new("FunctionNodeRotateRotation", rotation_space='LOCAL')
    g.links.new(g.sock(align.outputs, "Rotation"), g.sock(rotate.inputs, "Rotation"))
    g.links.new(euler.outputs[0], g.sock(rotate.inputs, "Rotate By"))

    # Sink into the ground along the normal
    size_bu = g.math('MULTIPLY', size_m, bu)
    setpos = g.new("GeometryNodeSetPosition")
    g.links.new(points, g.sock(setpos.inputs, "Geometry"))
    g.links.new(g.vmath('SCALE', normal, g.math('MULTIPLY', g.math('MULTIPLY', size_bu, i["Embed"]), -0.4)),
                g.sock(setpos.inputs, "Offset"))
    points = setpos.outputs[0]

    # Per-instance data for the rock material
    points = g.store(points, "RTG_Rock_Size_m", size_m)
    pz = g.split(g.named("RTG_Position_m", 'FLOAT_VECTOR'))[2]
    base_height = g.math('SUBTRACT', g.math('SUBTRACT', pz, i["Water Level (m)"]),
                         g.math('MULTIPLY', g.math('MULTIPLY', size_m, i["Embed"]), 0.4))
    points = g.store(points, "RTG_Height_Above_Water_m", base_height)

    coll = g.new("GeometryNodeCollectionInfo")
    g.links.new(i["Library"], g.sock(coll.inputs, "Collection"))
    g.sock(coll.inputs, "Separate Children").default_value = True
    g.sock(coll.inputs, "Reset Children").default_value = True
    inst = g.new("GeometryNodeInstanceOnPoints")
    g.links.new(points, g.sock(inst.inputs, "Points"))
    g.links.new(g.sock(coll.outputs, "Instances"), g.sock(inst.inputs, "Instance"))
    g.sock(inst.inputs, "Pick Instance").default_value = True
    g.feed(g.sock(inst.inputs, "Instance Index"), index)
    g.links.new(rotate.outputs[0], g.sock(inst.inputs, "Rotation"))
    g.feed(g.sock(inst.inputs, "Scale"), size_bu)
    go = g.new("NodeGroupOutput")
    g.links.new(g.sock(inst.outputs, "Instances"), go.inputs[0])
    return tree


def get_scatter_group():
    tree = bpy.data.node_groups.get(SCATTER_GROUP)
    if tree is not None and tree.get("rtg_version", 0) < SCATTER_VERSION:
        bpy.data.node_groups.remove(tree)
        tree = None
    return tree or _build_scatter_group()


def _set_input(mod, name, value):
    for item in mod.node_group.interface.items_tree:
        if getattr(item, "in_out", "") == 'INPUT' and item.name == name:
            mod[item.identifier] = value
            return


def find_rocks(terrain):
    return next((c for c in terrain.children if c.get("rtg_is_rocks")), None)


def ensure_rocks(context, terrain, mesh_bu_per_m: float, water_level_m: float):
    """Create or update the rock scatter object of a terrain."""
    rocks = find_rocks(terrain)
    if rocks is None:
        rocks = bpy.data.objects.new(ROCK_PREFIX + terrain.name, bpy.data.meshes.new(ROCK_PREFIX + terrain.name))
        for coll in terrain.users_collection:
            coll.objects.link(rocks)
        rocks.parent = terrain
        rocks["rtg_is_rocks"] = True
    mod = rocks.modifiers.get(SCATTER_MODIFIER) or rocks.modifiers.new(SCATTER_MODIFIER, 'NODES')
    if mod.node_group is None or mod.node_group.name != SCATTER_GROUP:
        mod.node_group = get_scatter_group()
    _set_input(mod, "Surface", terrain)
    _set_input(mod, "Library", get_rock_library(context))
    if context.scene.camera is not None:
        _set_input(mod, "Camera", context.scene.camera)
    update_rocks(rocks, mesh_bu_per_m, water_level_m)
    return rocks


def update_rocks(rocks, mesh_bu_per_m: float, water_level_m: float | None = None):
    mod = rocks.modifiers.get(SCATTER_MODIFIER)
    if mod is None or mod.node_group is None:
        return
    _set_input(mod, "Blender Units per Meter", float(mesh_bu_per_m))
    if water_level_m is not None:
        _set_input(mod, "Water Level (m)", float(water_level_m))
    rocks.update_tag()


def scatter_inputs(mod):
    """(label, idproperty key) of the user-facing scatter inputs."""
    names = {n for n, *_ in SCATTER_INPUTS}
    return [(item.name, item.identifier) for item in mod.node_group.interface.items_tree
            if getattr(item, "in_out", "") == 'INPUT' and item.name in names]
