"""Cliff meso-relief: a Geometry Nodes modifier that turns flat DEM walls into rock.

A 30 m DEM sampled as a height field leaves only a few vertex rows on a canyon wall, so
walls render as smooth sheets. This modifier adds the 5-80 m structure that reads as rock
from a distance:

* only steep faces are subdivided (fewer levels in the viewport than in renders), so plains
  and gentle slopes cost nothing;
* those faces are pushed horizontally, in real meters, by
  - vertical buttresses and flutes (dissolution runnels, joint-controlled ribs),
  - stepped ledges whose treads are flat enough for vegetation,
  - jointed blocks;
* the result is stored as ``RTG_Wall_Cavity`` (recesses, for shading) and ``RTG_Ledge``
  (wall ledges), and ledges are added to ``RTG_Plant_Density`` so plant scatterers that
  read the evaluated terrain (e.g. Procedural Plant Generator) populate them.

Displacement is zero at the edge of the subdivided region, so no cracks open between
subdivided and untouched faces.
"""

from __future__ import annotations

try:
    import bpy
    BLENDER_AVAILABLE = True
except ImportError:
    BLENDER_AVAILABLE = False

DETAIL_GROUP = "RTG Terrain Detail"
DETAIL_VERSION = 1
DETAIL_MODIFIER = "RTG Terrain Detail"

# (name, socket type, default, min, max, description)
DETAIL_INPUTS = (
    ("Wall Relief (m)", 'NodeSocketFloat', 10.0, 0.0, 100.0, "Depth of buttresses and flutes on steep walls"),
    ("Flute Spacing (m)", 'NodeSocketFloat', 35.0, 2.0, 500.0, "Horizontal spacing of buttresses / runnels"),
    ("Ledge Spacing (m)", 'NodeSocketFloat', 22.0, 2.0, 500.0, "Vertical spacing of stepped ledges"),
    ("Ledge Depth (m)", 'NodeSocketFloat', 3.0, 0.0, 50.0, "How far each ledge steps out"),
    ("Block Size (m)", 'NodeSocketFloat', 12.0, 1.0, 200.0, "Size of jointed blocks on the wall"),
    ("Wall Angle", 'NodeSocketFloat', 50.0, 20.0, 85.0, "Slopes steeper than this (degrees) get wall detail"),
    ("Viewport Subdivision", 'NodeSocketInt', 1, 0, 4, "Subdivision of wall faces in the viewport"),
    ("Render Subdivision", 'NodeSocketInt', 3, 0, 6, "Subdivision of wall faces in renders"),
    ("Blender Units per Meter", 'NodeSocketFloat', 1.0, 0.0, 1000.0, "Set automatically from the terrain scale"),
    ("Seed", 'NodeSocketFloat', 0.0, 0.0, 10000.0, "Variation of the wall pattern"),
)


class _GN:
    def __init__(self, tree):
        self.tree, self.nodes, self.links = tree, tree.nodes, tree.links
        self.x = 0

    def new(self, bl_idname, **props):
        node = self.nodes.new(bl_idname)
        node.location = (self.x, -40.0 * (len(self.nodes) % 30))
        self.x += 60
        for k, v in props.items():
            setattr(node, k, v)
        return node

    @staticmethod
    def sock(sockets, name):
        for s in sockets:
            if s.name == name and s.enabled:
                return s
        for s in sockets:
            if s.identifier == name:
                return s
        raise KeyError(name)

    def feed(self, sock, value):
        if hasattr(value, "is_output"):
            self.links.new(value, sock)
        else:
            sock.default_value = value

    def math(self, op, a, b=None, clamp=False):
        n = self.new("ShaderNodeMath", operation=op, use_clamp=clamp)
        self.feed(n.inputs[0], a)
        if b is not None:
            self.feed(n.inputs[1], b)
        return n.outputs[0]

    def smooth(self, value, lo, hi, to_min=0.0, to_max=1.0):
        n = self.new("ShaderNodeMapRange", data_type='FLOAT', interpolation_type='SMOOTHSTEP', clamp=True)
        for i, v in enumerate((value, lo, hi, to_min, to_max)):
            self.feed(n.inputs[i], v)
        return n.outputs[0]

    def vmath(self, op, a, b=None):
        n = self.new("ShaderNodeVectorMath", operation=op)
        self.feed(n.inputs[0], a)
        if b is not None:
            self.feed(self.sock(n.inputs, "Scale") if op == 'SCALE' else n.inputs[1], b)
        return n.outputs[0]

    def xyz(self, x, y, z):
        n = self.new("ShaderNodeCombineXYZ")
        for i, v in enumerate((x, y, z)):
            self.feed(n.inputs[i], v)
        return n.outputs[0]

    def split(self, vec):
        n = self.new("ShaderNodeSeparateXYZ")
        self.feed(n.inputs[0], vec)
        return n.outputs[0], n.outputs[1], n.outputs[2]

    def named(self, name, data_type):
        n = self.new("GeometryNodeInputNamedAttribute", data_type=data_type)
        self.sock(n.inputs, "Name").default_value = name
        return self.sock(n.outputs, "Attribute")

    def store(self, geometry, name, value, data_type='FLOAT'):
        n = self.new("GeometryNodeStoreNamedAttribute", data_type=data_type, domain='POINT')
        self.feed(self.sock(n.inputs, "Geometry"), geometry)
        self.sock(n.inputs, "Name").default_value = name
        self.feed(self.sock(n.inputs, "Value"), value)
        return n.outputs[0]


def _build_detail_group():
    tree = bpy.data.node_groups.new(DETAIL_GROUP, 'GeometryNodeTree')
    tree["rtg_version"] = DETAIL_VERSION
    iface = tree.interface
    iface.new_socket("Geometry", in_out='INPUT', socket_type='NodeSocketGeometry')
    for name, kind, default, lo, hi, desc in DETAIL_INPUTS:
        item = iface.new_socket(name, description=desc, in_out='INPUT', socket_type=kind)
        item.default_value, item.min_value, item.max_value = default, lo, hi
    iface.new_socket("Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')

    g = _GN(tree)
    gi = g.new("NodeGroupInput")
    inp = {s.name: s for s in gi.outputs}
    geo_in = gi.outputs[0]

    # --- Select wall faces (a 15° margin below Wall Angle, where displacement fades to zero)
    angle = inp["Wall Angle"]
    cos_wall = g.math('COSINE', g.math('RADIANS', angle))
    cos_margin = g.math('COSINE', g.math('RADIANS', g.math('SUBTRACT', angle, 15.0)))
    normal = g.sock(g.new("GeometryNodeInputNormal").outputs, "Normal")
    _, _, nz = g.split(normal)
    is_wall = g.math('LESS_THAN', g.math('ABSOLUTE', nz), cos_margin)

    sep = g.new("GeometryNodeSeparateGeometry", domain='FACE')
    g.links.new(geo_in, g.sock(sep.inputs, "Geometry"))
    g.links.new(is_wall, g.sock(sep.inputs, "Selection"))

    switch = g.new("GeometryNodeSwitch", input_type='INT')
    g.links.new(g.sock(g.new("GeometryNodeIsViewport").outputs, "Is Viewport"), g.sock(switch.inputs, "Switch"))
    g.links.new(inp["Render Subdivision"], g.sock(switch.inputs, "False"))
    g.links.new(inp["Viewport Subdivision"], g.sock(switch.inputs, "True"))
    subdiv = g.new("GeometryNodeSubdivideMesh")
    g.links.new(g.sock(sep.outputs, "Selection"), g.sock(subdiv.inputs, "Mesh"))
    g.links.new(g.sock(switch.outputs, "Output"), g.sock(subdiv.inputs, "Level"))

    join = g.new("GeometryNodeJoinGeometry")
    g.links.new(g.sock(subdiv.outputs, "Mesh"), join.inputs[0])
    g.links.new(g.sock(sep.outputs, "Inverted"), join.inputs[0])
    merge = g.new("GeometryNodeMergeByDistance")  # Mode defaults to "All"
    g.links.new(join.outputs[0], g.sock(merge.inputs, "Geometry"))
    g.sock(merge.inputs, "Distance").default_value = 1e-5
    mesh = merge.outputs[0]

    # --- Displacement in real meters (RTG_Position_m is interpolated by the subdivision)
    pos_m = g.named("RTG_Position_m", 'FLOAT_VECTOR')
    seed_offset = g.xyz(g.math('MULTIPLY', inp["Seed"], 17.31), g.math('MULTIPLY', inp["Seed"], 7.77), 0.0)
    p = g.vmath('ADD', pos_m, seed_offset)
    px, py, pz = g.split(p)
    wall = g.smooth(g.math('ABSOLUTE', nz), cos_margin, cos_wall, 0.0, 1.0)

    # Buttresses and flutes: noise stretched ~8x vertically
    inv_flute = g.math('DIVIDE', 1.0, inp["Flute Spacing (m)"])
    ribs_vec = g.xyz(g.math('MULTIPLY', px, inv_flute), g.math('MULTIPLY', py, inv_flute),
                     g.math('MULTIPLY', pz, g.math('MULTIPLY', inv_flute, 0.125)))
    ribs_noise = g.new("ShaderNodeTexNoise", noise_dimensions='3D', noise_type='FBM', normalize=True)
    g.links.new(ribs_vec, ribs_noise.inputs["Vector"])
    ribs_noise.inputs["Scale"].default_value = 1.0
    ribs_noise.inputs["Detail"].default_value = 4.0
    ribs_noise.inputs["Roughness"].default_value = 0.6
    ribs = g.math('MULTIPLY', g.math('SUBTRACT', ribs_noise.outputs[0], 0.5), g.math('MULTIPLY', inp["Wall Relief (m)"], 2.0))

    # Stepped ledges: saw-tooth along altitude, warped so ledges wander and pinch out
    warp_noise = g.new("ShaderNodeTexNoise", noise_dimensions='3D', noise_type='FBM', normalize=True)
    g.links.new(g.vmath('SCALE', p, 0.01), warp_noise.inputs["Vector"])
    warp_noise.inputs["Scale"].default_value = 1.0
    warp_noise.inputs["Detail"].default_value = 2.0
    phase = g.math('ADD', g.math('DIVIDE', pz, inp["Ledge Spacing (m)"]), g.math('MULTIPLY', warp_noise.outputs[0], 1.5))
    saw = g.math('FRACT', phase)
    ledges = g.math('MULTIPLY', g.math('SUBTRACT', saw, 0.5), inp["Ledge Depth (m)"])

    # Jointed blocks
    vor = g.new("ShaderNodeTexVoronoi", voronoi_dimensions='3D', feature='DISTANCE_TO_EDGE', distance='CHEBYCHEV')
    g.links.new(p, vor.inputs["Vector"])
    g.feed(vor.inputs["Scale"], g.math('DIVIDE', 1.0, inp["Block Size (m)"]))
    blocks = g.math('MULTIPLY', g.smooth(vor.outputs[0], 0.0, 0.15, -1.0, 0.0), g.math('MULTIPLY', inp["Block Size (m)"], 0.1))

    offset_m = g.math('MULTIPLY', g.math('ADD', g.math('ADD', ribs, ledges), blocks), wall)

    # Push outward along the horizontal part of the normal (walls grow buttresses, not bumps up)
    nx, ny, _ = g.split(normal)
    n_h = g.vmath('NORMALIZE', g.xyz(nx, ny, 0.0))
    offset = g.vmath('SCALE', n_h, g.math('MULTIPLY', offset_m, inp["Blender Units per Meter"]))
    setpos = g.new("GeometryNodeSetPosition")
    g.links.new(mesh, g.sock(setpos.inputs, "Geometry"))
    g.links.new(offset, g.sock(setpos.inputs, "Offset"))
    moved = setpos.outputs[0]

    # Recess factor for shading: 1 = deepest recess
    relief_total = g.math('MAXIMUM', g.math('ADD', g.math('ADD', inp["Wall Relief (m)"], inp["Ledge Depth (m)"]), 0.01), 0.01)
    cavity = g.math('MULTIPLY', g.smooth(offset_m, g.math('MULTIPLY', relief_total, -1.0), relief_total, 1.0, 0.0), wall)
    moved = g.store(moved, "RTG_Wall_Cavity", cavity)

    # Ledges: wall points whose displaced surface faces up
    new_normal = g.sock(g.new("GeometryNodeInputNormal").outputs, "Normal")
    _, _, new_nz = g.split(new_normal)
    ledge = g.math('MULTIPLY', g.smooth(new_nz, 0.4, 0.75), wall)
    moved = g.store(moved, "RTG_Ledge", ledge)
    density = g.math('MAXIMUM', g.named("RTG_Plant_Density", 'FLOAT'), g.math('MULTIPLY', ledge, 0.9))
    moved = g.store(moved, "RTG_Plant_Density", density)

    go = g.new("NodeGroupOutput")
    g.links.new(moved, go.inputs[0])
    return tree


def get_detail_group():
    tree = bpy.data.node_groups.get(DETAIL_GROUP)
    if tree is not None and tree.get("rtg_version", 0) < DETAIL_VERSION:
        bpy.data.node_groups.remove(tree)
        tree = None
    return tree or _build_detail_group()


def _set_input(mod, name, value):
    for item in mod.node_group.interface.items_tree:
        if getattr(item, "in_out", "") == 'INPUT' and item.name == name:
            mod[item.identifier] = value
            return


def get_detail_modifier(obj):
    return obj.modifiers.get(DETAIL_MODIFIER)


def apply_terrain_detail(obj, mesh_bu_per_m: float, enabled: bool):
    """Add (first in the stack) or remove the cliff detail modifier and keep its scale in sync."""
    mod = get_detail_modifier(obj)
    if not enabled:
        if mod is not None:
            obj.modifiers.remove(mod)
        return None
    if mod is None:
        mod = obj.modifiers.new(DETAIL_MODIFIER, 'NODES')
        mod.node_group = get_detail_group()
        index = list(obj.modifiers).index(mod)
        if index > 0:
            obj.modifiers.move(index, 0)
    update_detail_scale(obj, mesh_bu_per_m)
    return mod


def update_detail_scale(obj, mesh_bu_per_m: float):
    """Detail is authored in meters; the modifier works in object space."""
    mod = get_detail_modifier(obj)
    if mod is not None and mod.node_group is not None:
        _set_input(mod, "Blender Units per Meter", float(mesh_bu_per_m))
        obj.update_tag()


def detail_inputs(mod):
    """(label, idproperty key) pairs for drawing the modifier inputs in the sidebar."""
    return [(item.name, item.identifier) for item in mod.node_group.interface.items_tree
            if getattr(item, "in_out", "") == 'INPUT' and item.socket_type != 'NodeSocketGeometry'
            and item.name != "Blender Units per Meter"]
