"""Procedural terrain material driven by the RTG mesh attributes (EEVEE and Cycles).

The "RTG Terrain Surface" node group turns the per-vertex geology into a surface:

* Rock micro-relief summed from weathering terms (blocky joints, columnar prisms,
  layering, rounded corestones, vesicles, karst, grain) weighted by the
  ``RTG_Rock_Fracture`` / ``RTG_Rock_Surface`` attributes and sized in meters.
* Large-scale structure readable from far away: bed sets tens of meters thick with
  varying thickness (layered rocks) and vertical runoff streaks on steep walls.
* Cover layers from the terrain masks: scree, soil from the parent rock, ground
  vegetation, wet stream beds and snow, with noisy edges.

Performance: each terrain gets a group variant containing only the terms its rocks use
(a single-lithology plain needs one or two; a canyon in limestone three) and a quality
level (fewer noise octaves in Preview). Variants are shared by name. All textures use
``RTG_Position_m`` (real meters), so detail has a physical size at any scene scale and
3D procedural textures never stretch on cliffs.
"""

from __future__ import annotations

try:
    import bpy
    BLENDER_AVAILABLE = True
except ImportError:
    BLENDER_AVAILABLE = False

from ..core.geology.rock_masks import SURFACE_TERMS

GROUP_NAME = "RTG Terrain Surface"
GROUP_VERSION = 4
SUBDIV_MODIFIER = "RTG Displacement"
BU_PER_M_NODE = "RTG_BU_per_m"
SURFACE_NODE = "RTG_Surface"

# Inputs set by code (not shown as sliders)
HIDDEN_INPUTS = (
    ("Water Level (m)", -100000.0, -100000.0, 10000.0, "Altitude of the water surface; set by the Water Surface operator"),
)

# Noise octaves per quality level: (macro relief, color tone, fine grain)
QUALITY_DETAIL = {
    "PREVIEW": (3.0, 2.0, 1.0),
    "FINAL": (8.0, 4.0, 4.0),
}

# (name, default, min, max, description) of the group inputs shown in the sidebar
SURFACE_CONTROLS = (
    ("Relief Strength", 1.0, 0.0, 5.0, "Strength of the rock micro-relief (bump or displacement)"),
    ("Detail Scale", 1.0, 0.1, 10.0, "Frequency multiplier of all rock textures"),
    ("Weathering", 0.6, 0.0, 1.0, "Color variation between weathered and fresh rock"),
    ("Strata Contrast", 0.5, 0.0, 1.0, "Visibility of bed sets on cliffs (layered rocks)"),
    ("Wall Streaks", 0.5, 0.0, 1.0, "Vertical runoff staining on steep walls"),
    ("Iron Staining", 0.4, 0.0, 1.0, "Ochre / orange oxide patches on walls"),
    ("Moss & Lichen", 0.5, 0.0, 1.0, "Dark green-grey growth in wall recesses and on damp or shaded rock"),
    ("Cavity Shading", 0.6, 0.0, 1.0, "Darkening of wall recesses from the Terrain Detail modifier"),
    ("Waterline Wetness", 0.7, 0.0, 1.0, "Dark, glossy band of wet rock just above the water and silt-tinted submerged ground"),
    ("Wet Band (m)", 1.5, 0.0, 20.0, "Height of the wet band above the water level"),
    ("Scree Cover", 1.0, 0.0, 2.0, "Amount of loose scree on talus slopes"),
    ("Soil Cover", 1.0, 0.0, 2.0, "Soil on gentle slopes (derived from the parent rock)"),
    ("Vegetation Cover", 1.0, 0.0, 2.0, "Ground vegetation from the plant density mask"),
    ("Wet Streams", 1.0, 0.0, 2.0, "Darker, glossy stream beds from the water flow mask"),
    ("Snow Amount", 1.0, 0.0, 2.0, "Snow cover from the snow mask"),
)


class _Graph:
    """Tiny helper to build node graphs with readable code (auto-placed in columns)."""

    def __init__(self, tree):
        self.tree = tree
        self.nodes = tree.nodes
        self.links = tree.links
        self._col = 0
        self._row = 0

    def new(self, bl_idname, label="", **props):
        node = self.nodes.new(bl_idname)
        node.location = (self._col * 180.0, -self._row * 40.0)
        self._row += 1
        if self._row > 60:
            self._row, self._col = 0, self._col + 1
        if label:
            node.label = label
        for key, value in props.items():
            setattr(node, key, value)
        return node

    def column(self):
        self._row, self._col = 0, self._col + 1

    @staticmethod
    def socket(sockets, *names):
        for name in names:
            for s in sockets:
                if s.identifier == name or (s.name == name and s.enabled):
                    return s
        raise KeyError(f"No socket {names}")

    def feed(self, sock, value):
        if hasattr(value, "is_output"):
            self.links.new(value, sock)
        else:
            sock.default_value = value

    def attr(self, name):
        return self.new("ShaderNodeAttribute", label=name, attribute_type='GEOMETRY', attribute_name=name)

    def attr_fac(self, name):
        return self.socket(self.attr(name).outputs, "Fac", "Factor")

    def split(self, color_socket):
        node = self.new("ShaderNodeSeparateColor")
        self.links.new(color_socket, node.inputs[0])
        return node.outputs[0], node.outputs[1], node.outputs[2]

    def math(self, op, a, b=None, clamp=False):
        node = self.new("ShaderNodeMath", operation=op, use_clamp=clamp)
        self.feed(node.inputs[0], a)
        if b is not None:
            self.feed(node.inputs[1], b)
        return node.outputs[0]

    def mul(self, a, b):
        return self.math('MULTIPLY', a, b)

    def add(self, a, b, clamp=False):
        return self.math('ADD', a, b, clamp=clamp)

    def smooth(self, value, lo, hi, to_min=0.0, to_max=1.0):
        node = self.new("ShaderNodeMapRange", data_type='FLOAT', interpolation_type='SMOOTHSTEP', clamp=True)
        for i, v in enumerate((value, lo, hi, to_min, to_max)):
            self.feed(node.inputs[i], v)
        return node.outputs[0]

    def mix(self, fac, a, b, data_type='RGBA', blend='MIX'):
        node = self.new("ShaderNodeMix", data_type=data_type, blend_type=blend, clamp_factor=True)
        kind = "Color" if data_type == 'RGBA' else "Float"
        self.feed(self.socket(node.inputs, "Factor_Float"), fac)
        self.feed(self.socket(node.inputs, f"A_{kind}"), a)
        self.feed(self.socket(node.inputs, f"B_{kind}"), b)
        return self.socket(node.outputs, f"Result_{kind}")

    def mixf(self, fac, a, b):
        return self.mix(fac, a, b, data_type='FLOAT')

    def gray(self, value):
        node = self.new("ShaderNodeCombineColor")
        for i in range(3):
            self.feed(node.inputs[i], value)
        return node.outputs[0]

    def scale_vec(self, vec, factor):
        node = self.new("ShaderNodeVectorMath", operation='SCALE')
        self.feed(node.inputs[0], vec)
        self.feed(self.socket(node.inputs, "Scale"), factor)
        return node.outputs[0]

    def mul_vec(self, vec, xyz):
        node = self.new("ShaderNodeVectorMath", operation='MULTIPLY')
        self.feed(node.inputs[0], vec)
        node.inputs[1].default_value = xyz
        return node.outputs[0]

    def noise(self, vec, scale, detail=2.0, roughness=0.55, distortion=0.0, color=False):
        node = self.new("ShaderNodeTexNoise", noise_dimensions='3D', noise_type='FBM', normalize=True)
        self.feed(node.inputs["Vector"], vec)
        self.feed(node.inputs["Scale"], scale)
        node.inputs["Detail"].default_value = detail
        node.inputs["Roughness"].default_value = roughness
        node.inputs["Distortion"].default_value = distortion
        return self.split(node.outputs[1]) if color else node.outputs[0]

    def voronoi(self, vec, scale, feature, distance='EUCLIDEAN', randomness=1.0):
        node = self.new("ShaderNodeTexVoronoi", voronoi_dimensions='3D', feature=feature, distance=distance)
        self.feed(node.inputs["Vector"], vec)
        self.feed(node.inputs["Scale"], scale)
        node.inputs["Randomness"].default_value = randomness
        if feature == 'SMOOTH_F1':
            node.inputs["Smoothness"].default_value = 1.0
        return node.outputs[0]

    def bands(self, pos, phase, distortion, detail):
        """Saw-tooth bands along ``phase`` (one band per unit), wobbled horizontally by ``pos``."""
        wobble = self.mul_vec(pos, (0.02, 0.02, 0.0))
        xyz = self.new("ShaderNodeSeparateXYZ")
        self.links.new(wobble, xyz.inputs[0])
        vec = self.new("ShaderNodeCombineXYZ")
        self.links.new(xyz.outputs[0], vec.inputs[0])
        self.links.new(xyz.outputs[1], vec.inputs[1])
        # Blender's Z bands repeat every ~0.314 units
        self.feed(vec.inputs[2], self.mul(phase, 0.314))
        wave = self.new("ShaderNodeTexWave", wave_type='BANDS', bands_direction='Z', wave_profile='SAW')
        self.links.new(vec.outputs[0], wave.inputs["Vector"])
        wave.inputs["Scale"].default_value = 1.0
        wave.inputs["Distortion"].default_value = distortion
        wave.inputs["Detail"].default_value = detail
        return self.socket(wave.outputs, "Fac", "Factor")

    def total(self, terms):
        result = terms[0]
        for t in terms[1:]:
            result = self.add(result, t)
        return result


def group_name(terms, quality: str) -> str:
    return f"{GROUP_NAME} [{'+'.join(terms) or 'plain'}] {quality.title()}"


def _build_surface_group(terms, quality: str):
    group = bpy.data.node_groups.new(group_name(terms, quality), 'ShaderNodeTree')
    group["rtg_version"] = GROUP_VERSION
    iface = group.interface
    for name, default, lo, hi, desc in SURFACE_CONTROLS + HIDDEN_INPUTS:
        item = iface.new_socket(name, description=desc, in_out='INPUT', socket_type='NodeSocketFloat')
        item.default_value, item.min_value, item.max_value = default, lo, hi
    iface.new_socket("Color", in_out='OUTPUT', socket_type='NodeSocketColor')
    iface.new_socket("Roughness", in_out='OUTPUT', socket_type='NodeSocketFloat')
    iface.new_socket("Height (m)", in_out='OUTPUT', socket_type='NodeSocketFloat')

    oct_macro, oct_tone, oct_fine = QUALITY_DETAIL[quality]
    g = _Graph(group)
    gi = g.new("NodeGroupInput")
    ctrl = {name: gi.outputs[name] for name, *_ in SURFACE_CONTROLS + HIDDEN_INPUTS}
    detail = ctrl["Detail Scale"]

    # --- Attributes -------------------------------------------------------
    pos = g.socket(g.attr("RTG_Position_m").outputs, "Vector")
    rock_color = g.socket(g.attr("RTG_Rock_Color").outputs, "Color")
    rock_alt = g.socket(g.attr("RTG_Rock_Color_Alt").outputs, "Color")
    fracture_attr = g.attr("RTG_Rock_Fracture")
    surface_attr = g.attr("RTG_Rock_Surface")
    weight = dict(zip(SURFACE_TERMS[:3], g.split(g.socket(fracture_attr.outputs, "Color"))))
    weight["rounded"] = g.socket(fracture_attr.outputs, "Alpha")
    weight.update(zip(SURFACE_TERMS[4:], g.split(g.socket(surface_attr.outputs, "Color"))))
    rock_rough = g.socket(surface_attr.outputs, "Alpha")
    joint = g.math('MAXIMUM', g.attr_fac("RTG_Rock_Joint_m"), 0.05)
    hardness = g.attr_fac("RTG_Hardness")
    geo = g.attr("RTG_Geo_Masks")
    flow, snow, talus = g.split(g.socket(geo.outputs, "Color"))
    wetness = g.socket(geo.outputs, "Alpha")
    plants, slope, valley = g.split(g.socket(g.attr("RTG_Eco_Masks").outputs, "Color"))

    p_joint = g.scale_vec(pos, g.math('DIVIDE', detail, joint))
    g.column()

    # --- Rock micro-relief (meters): only the terms this terrain uses -------
    heights = [g.mul(g.add(g.noise(pos, g.mul(detail, 0.25), detail=oct_macro), -0.5), g.mul(joint, 0.5))]
    cracks = []
    band = None
    if "blocky" in terms:
        d = g.voronoi(p_joint, 1.0, 'DISTANCE_TO_EDGE', 'CHEBYCHEV')
        heights.append(g.mul(g.mul(g.smooth(d, 0.0, 0.12), g.mul(joint, 0.15)), weight["blocky"]))
        cracks.append(g.mul(g.smooth(d, 0.0, 0.08, 1.0, 0.0), weight["blocky"]))
    if "columnar" in terms:
        d = g.voronoi(g.mul_vec(p_joint, (1.0, 1.0, 0.15)), 1.0, 'DISTANCE_TO_EDGE', randomness=0.7)
        heights.append(g.mul(g.mul(g.smooth(d, 0.0, 0.08), g.mul(joint, 0.2)), weight["columnar"]))
        cracks.append(g.mul(g.smooth(d, 0.0, 0.06, 1.0, 0.0), weight["columnar"]))
    if "layered" in terms:
        bedding = g.math('MAXIMUM', g.attr_fac("RTG_Rock_Bedding_m"), 0.02)
        depth = g.attr_fac("RTG_Strata_Depth_m")
        band = g.bands(pos, g.math('DIVIDE', depth, bedding), 1.5, oct_tone)
        heights.append(g.mul(g.mul(band, g.mul(g.math('MINIMUM', bedding, 2.0), 0.15)), weight["layered"]))
    if "rounded" in terms:
        d = g.voronoi(p_joint, 1.0, 'SMOOTH_F1')
        heights.append(g.mul(g.mul(g.math('SUBTRACT', 1.0, d), g.mul(joint, 0.3)), weight["rounded"]))
    if "vesicular" in terms:
        d = g.voronoi(pos, g.mul(detail, 20.0), 'F1')
        heights.append(g.mul(g.mul(g.smooth(d, 0.0, 0.35, -1.0, 0.0), 0.02), weight["vesicular"]))
    if "karst" in terms:
        d = g.voronoi(g.mul_vec(p_joint, (1.0, 1.0, 0.3)), 1.0, 'DISTANCE_TO_EDGE')
        grike = g.mul(g.smooth(d, 0.0, 0.05, 1.0, 0.0), weight["karst"])
        heights.append(g.mul(grike, g.mul(joint, -0.2)))
        cracks.append(grike)
    if "granular" in terms:
        grain = g.noise(pos, g.mul(detail, 8.0), detail=oct_fine)
        heights.append(g.mul(g.mul(g.add(grain, -0.5), 0.03), weight["granular"]))
    rock_height = g.total(heights)
    g.column()

    # --- Rock color ---------------------------------------------------------
    # One 3-channel noise gives three independent fields: tone, edge breakup, vegetation breakup
    tone, breakup, breakup_b = g.noise(pos, 0.06, detail=oct_tone, color=True)
    col = g.mix(g.mul(g.smooth(tone, 0.35, 0.7), ctrl["Weathering"]), rock_color, rock_alt)
    if band is not None:
        col = g.mix(g.mul(g.mul(band, weight["layered"]), 0.5), col, rock_alt)

    # Bed sets: tens of meters thick, varying thickness, visible from far away on canyon walls.
    # Cut by the strata depth, so they follow real layer geometry when a layer stack is used.
    if "layered" in terms:
        depth = g.attr_fac("RTG_Strata_Depth_m")
        sets = g.bands(pos, g.math('DIVIDE', depth, g.add(12.0, g.mul(breakup, 16.0))), 3.0, 2.0)
        set_tone = g.mul(g.smooth(sets, 0.2, 0.8), ctrl["Strata Contrast"])
        col = g.mix(set_tone, col, rock_alt)
        col = g.mix(g.mul(set_tone, 0.5), col, g.gray(0.75), blend='MULTIPLY')
        rock_height = g.add(rock_height, g.mul(g.mul(set_tone, weight["layered"]), 0.4))

    # Vertical runoff streaks (water staining, biofilm, desert varnish) on steep walls
    steep = g.smooth(slope, 0.45, 0.7)
    streak = g.noise(g.mul_vec(pos, (1.0, 1.0, 0.04)), 0.12, detail=2.0)
    stain = g.mul(g.mul(g.smooth(streak, 0.45, 0.75), steep), ctrl["Wall Streaks"])
    col = g.mix(stain, col, g.gray(0.55), blend='MULTIPLY')

    # Wall meso-relief from the Terrain Detail modifier (0 when it is absent)
    cavity = g.attr_fac("RTG_Wall_Cavity")
    ledge = g.attr_fac("RTG_Ledge")
    col = g.mix(g.mul(cavity, g.mul(ctrl["Cavity Shading"], 0.6)), col, g.gray(0.35), blend='MULTIPLY')

    # Iron oxide staining: large ochre patches dripping down the walls
    iron = g.noise(g.mul_vec(pos, (1.0, 1.0, 0.15)), 0.025, detail=3.0)
    iron_f = g.mul(g.mul(g.smooth(iron, 0.55, 0.75), steep), ctrl["Iron Staining"])
    col = g.mix(iron_f, col, (0.47, 0.25, 0.10, 1.0), blend='MULTIPLY')

    # Moss and lichen where walls stay damp: recesses, wet ground, ledges
    damp = g.add(g.add(g.mul(cavity, 0.7), g.mul(wetness, 0.4)), g.mul(ledge, 0.5), clamp=True)
    moss_f = g.smooth(g.add(g.mul(damp, ctrl["Moss & Lichen"]), g.mul(g.add(breakup, -0.5), 0.3)), 0.25, 0.55)
    col = g.mix(moss_f, col, (0.06, 0.075, 0.04, 1.0))

    if cracks:
        crack = g.math('MINIMUM', g.total(cracks), 1.0)
        col = g.mix(1.0, col, g.gray(g.math('SUBTRACT', 1.0, g.mul(crack, 0.45))), blend='MULTIPLY')
    if quality == "FINAL":
        speckle = g.noise(pos, g.mul(detail, 30.0), detail=oct_fine)
        col = g.mix(1.0, col, g.gray(g.add(0.92, g.mul(speckle, 0.16))), blend='MULTIPLY')
    g.column()

    # --- Cover layers -------------------------------------------------------
    jitter = g.mul(g.add(breakup, -0.5), 0.4)
    jitter_b = g.mul(g.add(breakup_b, -0.5), 0.4)

    # Scree: loose fragments of the local rock
    scree_f = g.smooth(g.add(g.mul(talus, ctrl["Scree Cover"]), jitter), 0.25, 0.55)
    col = g.mix(scree_f, col, g.mix(0.5, rock_color, rock_alt))
    height = g.mixf(scree_f, rock_height, g.mul(g.add(tone, -0.5), 0.08))
    rough = g.mixf(scree_f, rock_rough, 0.9)

    # Soil on gentle slopes: parent-rock color darkened by organic matter and moisture
    flatness = g.math('MAXIMUM', g.smooth(slope, 0.28, 0.45, 1.0, 0.0), ledge)
    soil_amount = g.mul(g.mul(flatness, ctrl["Soil Cover"]), g.math('SUBTRACT', 1.0, g.mul(hardness, 0.3)))
    soil_f = g.smooth(g.add(soil_amount, jitter), 0.35, 0.65)
    soil_col = g.mix(0.65, rock_color, (0.08, 0.06, 0.045, 1.0))
    soil_col = g.mix(g.mul(wetness, 0.35), soil_col, (0.0, 0.0, 0.0, 1.0))
    col = g.mix(soil_f, col, soil_col)
    height = g.mixf(soil_f, height, 0.0)
    rough = g.mixf(soil_f, rough, 0.95)

    # Ground vegetation: dry to green with moisture
    veg_f = g.smooth(g.add(g.mul(g.math('MAXIMUM', plants, g.mul(ledge, 0.8)), ctrl["Vegetation Cover"]), jitter_b), 0.3, 0.6)
    moist = g.add(wetness, g.mul(valley, 0.5), clamp=True)
    veg_col = g.mix(moist, (0.10, 0.09, 0.04, 1.0), (0.03, 0.06, 0.015, 1.0))
    veg_col = g.mix(1.0, veg_col, g.gray(g.add(0.7, g.mul(breakup_b, 0.6))), blend='MULTIPLY')
    col = g.mix(veg_f, col, veg_col)
    height = g.mixf(veg_f, height, g.mul(breakup_b, 0.05))
    rough = g.mixf(veg_f, rough, 0.85)

    # Waterline: wet band above the water surface, silt-tinted ground below it
    pz = g.new("ShaderNodeSeparateXYZ")
    g.links.new(pos, pz.inputs[0])
    above = g.math('SUBTRACT', pz.outputs[2], ctrl["Water Level (m)"])
    band = g.math('MAXIMUM', ctrl["Wet Band (m)"], 0.01)
    wet_line = g.mul(g.smooth(g.add(above, g.mul(jitter, band)), band, 0.0), ctrl["Waterline Wetness"])
    submerged = g.mul(g.smooth(above, 0.0, -1.0), ctrl["Waterline Wetness"])
    col = g.mix(g.mul(wet_line, 0.5), col, (0.0, 0.0, 0.0, 1.0))
    col = g.mix(submerged, col, (0.30, 0.33, 0.22, 1.0), blend='MULTIPLY')
    rough = g.mixf(g.math('MAXIMUM', wet_line, submerged), rough, 0.12)

    # Wet stream beds: darker and glossy
    wet_f = g.smooth(g.mul(flow, ctrl["Wet Streams"]), 0.5, 0.8)
    col = g.mix(g.mul(wet_f, 0.45), col, (0.0, 0.0, 0.0, 1.0))
    rough = g.mixf(wet_f, rough, 0.15)

    # Snow on top, smoothing the relief underneath
    snow_f = g.smooth(g.add(g.mul(snow, ctrl["Snow Amount"]), jitter), 0.3, 0.55)
    col = g.mix(snow_f, col, (0.85, 0.87, 0.9, 1.0))
    height = g.mixf(snow_f, height, g.mul(height, 0.3))
    rough = g.mixf(snow_f, rough, 0.45)

    height = g.mul(height, ctrl["Relief Strength"])
    g.column()
    go = g.new("NodeGroupOutput")
    g.links.new(col, go.inputs["Color"])
    g.links.new(rough, go.inputs["Roughness"])
    g.links.new(height, go.inputs["Height (m)"])
    return group


def get_surface_group(terms, quality: str):
    """Group variant for these terms and quality (built once, then shared)."""
    name = group_name(terms, quality)
    group = bpy.data.node_groups.get(name)
    if group is not None and group.get("rtg_version", 0) < GROUP_VERSION:
        bpy.data.node_groups.remove(group)
        group = None
    return group or _build_surface_group(terms, quality)


def build_terrain_material(name: str):
    mat = bpy.data.materials.new(name)
    mat["rtg_terrain_material"] = True
    if hasattr(mat, "use_nodes"):
        mat.use_nodes = True
    tree = mat.node_tree
    tree.nodes.clear()
    g = _Graph(tree)
    surface = g.new("ShaderNodeGroup", label="Terrain Surface")
    surface.name = SURFACE_NODE
    bu = g.new("ShaderNodeValue", label="Blender units per meter")
    bu.name = BU_PER_M_NODE
    g.column()
    height_bu = g.math('MULTIPLY', 0.0, bu.outputs[0])
    bsdf = g.new("ShaderNodeBsdfPrincipled")
    disp = g.new("ShaderNodeDisplacement", space='WORLD')
    disp.inputs["Midlevel"].default_value = 0.0
    disp.inputs["Scale"].default_value = 1.0
    g.column()
    out = g.new("ShaderNodeOutputMaterial")
    g.links.new(height_bu, disp.inputs["Height"])
    g.links.new(bsdf.outputs[0], out.inputs["Surface"])
    g.links.new(disp.outputs[0], out.inputs["Displacement"])
    return mat


def set_surface_variant(mat, terms, quality: str):
    """Point the material at the group variant for ``terms`` / ``quality``, keeping slider values."""
    tree = mat.node_tree
    surface = tree.nodes.get(SURFACE_NODE)
    group = get_surface_group(terms, quality)
    mat["rtg_terms"] = ",".join(terms)
    if surface.node_tree == group:
        return
    values = {s.name: s.default_value for s in surface.inputs} if surface.node_tree else {}
    surface.node_tree = group
    for s in surface.inputs:
        if s.name in values:
            s.default_value = values[s.name]
    bsdf = next(n for n in tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
    height_bu = tree.nodes[BU_PER_M_NODE].outputs[0].links[0].to_node
    tree.links.new(surface.outputs["Color"], bsdf.inputs["Base Color"])
    tree.links.new(surface.outputs["Roughness"], bsdf.inputs["Roughness"])
    tree.links.new(surface.outputs["Height (m)"], height_bu.inputs[0])


def get_surface_node(obj):
    mat = obj.active_material
    if not mat or not mat.node_tree:
        return None
    return mat.node_tree.nodes.get(SURFACE_NODE)


def update_material_scale(obj, bu_per_m: float):
    """Keep the micro-relief physically sized when the terrain scale changes."""
    mat = obj.active_material
    node = mat.node_tree.nodes.get(BU_PER_M_NODE) if mat and mat.node_tree else None
    if node:
        node.outputs[0].default_value = bu_per_m


def apply_terrain_material(obj, bu_per_m: float, displacement: str, terms, quality: str):
    """Give a terrain its RTG material (created on first use), surface variant and displacement setup."""
    mat = obj.active_material
    if not mat or not mat.get("rtg_terrain_material"):
        mat = build_terrain_material(f"RTG_{obj.name}")
        obj.data.materials.clear()
        obj.data.materials.append(mat)
    set_surface_variant(mat, tuple(terms), quality)
    update_material_scale(obj, bu_per_m)

    mat.displacement_method = 'BOTH' if displacement == 'DISPLACEMENT' else 'BUMP'
    mod = obj.modifiers.get(SUBDIV_MODIFIER)
    if displacement == 'DISPLACEMENT':
        if mod is None:
            mod = obj.modifiers.new(SUBDIV_MODIFIER, 'SUBSURF')
        mod.subdivision_type = 'SIMPLE'       # Keep the DEM shape; only add vertices for displacement
        mod.levels = 0
        mod.render_levels = 3
        if hasattr(mod, "use_adaptive_subdivision"):
            mod.use_adaptive_subdivision = True
    elif mod is not None:
        obj.modifiers.remove(mod)
    return mat


def set_quality_everywhere(quality: str):
    """Switch every RTG terrain material in the file to ``quality`` (e.g. Final before rendering)."""
    for mat in bpy.data.materials:
        if mat.get("rtg_terrain_material") and mat.node_tree and mat.node_tree.nodes.get(SURFACE_NODE):
            terms = tuple(t for t in mat.get("rtg_terms", "").split(",") if t)
            set_surface_variant(mat, terms, quality)


def set_material_water_level(obj, level_m: float):
    """Tell the terrain material where the water surface is (wet band, submerged tint)."""
    surface = get_surface_node(obj)
    if surface is not None and "Water Level (m)" in surface.inputs:
        surface.inputs["Water Level (m)"].default_value = float(level_m)
