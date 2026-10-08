"""Blender 3D Viewport Sidebar panels for Real Terrain Generator."""

from __future__ import annotations

try:
    import bpy
    BLENDER_AVAILABLE = True
except ImportError:
    BLENDER_AVAILABLE = False

import json

from ..core.geo import meters_per_pixel, count_tiles_for_area
from ..core.geology.lithology import profile_for
from .preferences import get_opentopography_key

# Tile count above which the download size is flagged
LARGE_DOWNLOAD_TILES = 64

# (vertex group, label, icon) in the order shown in the mask grid
MASK_BUTTONS = (
    ("RTG_Plant_Density", "Vegetation", 'OUTLINER_OB_CURVES'),
    ("RTG_Water_Flow", "Water Flow", 'FORCE_DRAG'),
    ("RTG_Wetness", "Wetness", 'MOD_FLUID'),
    ("RTG_Snow", "Snow", 'FREEZE'),
    ("RTG_Talus", "Scree", 'STICKY_UVS_LOC'),
    ("RTG_Roughness", "Ruggedness", 'MOD_DISPLACE'),
    ("RTG_Slope", "Slope", 'SNAP_FACE'),
    ("RTG_Elevation", "Elevation", 'CON_TRANSLIKE'),
    ("RTG_Valley", "Valleys", 'CON_ROTLIKE'),
)


if BLENDER_AVAILABLE:
    from .operators import get_active_terrain, resolve_zoom
    from .materials import SURFACE_CONTROLS, get_surface_node
    from .terrain_detail import get_detail_modifier, detail_inputs
    from .water import WATER_CONTROLS, WATER_COLORS, get_water_node, find_water
    from .rocks import ROCK_CONTROLS, SCATTER_MODIFIER, find_rocks, scatter_inputs, get_rock_surface_node

    class RTG_PanelBase:
        bl_space_type = 'VIEW_3D'
        bl_region_type = 'UI'
        bl_category = "Real Terrain"


    class RTG_PT_MainPanel(RTG_PanelBase, bpy.types.Panel):
        """Selected terrain summary."""
        bl_label = "Real Terrain"
        bl_idname = "RTG_PT_MainPanel"

        def draw(self, context):
            layout = self.layout
            props = context.scene.rtg_properties
            obj = get_active_terrain(context)

            if not obj:
                col = layout.column(align=True)
                col.label(text="No terrain selected", icon='INFO')
                col.label(text="Set a place, then Generate")
                return

            box = layout.box()
            box.label(text=obj.name, icon='MESH_GRID')
            col = box.column(align=True)
            col.scale_y = 0.8
            w_km = obj.get("rtg_width_m", 0.0) / 1000.0
            h_km = obj.get("rtg_height_m", 0.0) / 1000.0
            col.label(text=f"{w_km:.1f} x {h_km:.1f} km")
            col.label(text=f"Altitude {obj.get('rtg_elevation_min_m', 0.0):.0f} to {obj.get('rtg_elevation_max_m', 0.0):.0f} m")
            if obj.get("rtg_attribution"):
                col.label(text=f"Data: {obj['rtg_attribution']}")

            row = layout.row(align=True)
            row.prop(props, "live_preview", toggle=True, icon='PLAY' if props.live_preview else 'PAUSE')
            row.operator("rtg.export_heightmap", text="Heightmap", icon='EXPORT')


    class RTG_PT_DataFetch(RTG_PanelBase, bpy.types.Panel):
        """Location, area and elevation source of the next terrain."""
        bl_label = "Location & Data"
        bl_idname = "RTG_PT_DataFetch"
        bl_parent_id = "RTG_PT_MainPanel"

        def draw(self, context):
            layout = self.layout
            props = context.scene.rtg_properties

            row = layout.row(align=True)
            row.prop(props, "location_search", text="", icon='VIEWZOOM')
            row.operator("rtg.search_location", text="Search")
            if props.search_status:
                sub = layout.row()
                sub.scale_y = 0.8
                sub.label(text=props.search_status, icon='PINNED')

            col = layout.column(align=True)
            col.prop(props, "latitude")
            col.prop(props, "longitude")
            row = layout.row(align=True)
            row.prop(props, "area_width_km", text="W km")
            row.prop(props, "area_height_km", text="L km")

            layout.separator()
            layout.prop(props, "provider", text="Source")
            if props.provider == "OPENTOPOGRAPHY":
                layout.prop(props, "opentopography_dataset", text="Dataset")
                if not get_opentopography_key(context):
                    row = layout.row(align=True)
                    row.alert = True
                    row.operator("rtg.open_preferences", text="Set API Key", icon='ERROR')
                    row.operator("wm.url_open", text="", icon='URL').url = "https://portal.opentopography.org/myopentopo"

            layout.prop(props, "grid_resolution", text="Resolution")
            self.draw_detail_info(layout, props)

            row = layout.row()
            row.scale_y = 1.5
            row.operator("rtg.generate_terrain", icon='SURFACE_DATA')

        @staticmethod
        def draw_detail_info(layout, props):
            """Source data density vs mesh density, and download size for AWS tiles."""
            mesh_res = int(props.grid_resolution)
            span_m = max(props.area_width_km, props.area_height_km) * 1000.0

            if props.provider == "AWS_TERRARIUM":
                row = layout.row(align=True)
                row.prop(props, "auto_zoom", text="", icon='AUTO', toggle=True)
                sub = row.row(align=True)
                sub.enabled = not props.auto_zoom
                sub.prop(props, "zoom_level")
                zoom = resolve_zoom(props)
                source_m = meters_per_pixel(props.latitude, zoom)
                tiles = count_tiles_for_area(props.latitude, props.longitude, props.area_width_km, props.area_height_km, zoom)
            else:
                source_m = 90.0 if props.opentopography_dataset == "SRTMGL3" else 30.0
                tiles = 0

            col = layout.column(align=True)
            col.scale_y = 0.8
            mesh_m = span_m / max(1, mesh_res - 1)
            col.label(text=f"Data {source_m:.0f} m · Mesh {mesh_m:.0f} m", icon='DRIVER')
            if source_m > mesh_m * 1.5:
                col.label(text="Mesh denser than data", icon='INFO')
            if tiles > LARGE_DOWNLOAD_TILES:
                col.alert = True
                col.label(text=f"{tiles} tiles to download", icon='ERROR')


    class RTG_PT_ScaleGeometry(RTG_PanelBase, bpy.types.Panel):
        """Scene scale and vertical relief."""
        bl_label = "Scale & Relief"
        bl_idname = "RTG_PT_ScaleGeometry"
        bl_parent_id = "RTG_PT_MainPanel"

        def draw(self, context):
            layout = self.layout
            props = context.scene.rtg_properties
            obj = get_active_terrain(context)

            layout.row().prop(props, "scale_mode", expand=True)
            if props.scale_mode == "FIT":
                layout.prop(props, "target_size_m")
            elif props.scale_mode == "CUSTOM":
                row = layout.row(align=True)
                row.label(text="Scale 1 :")
                row.prop(props, "scale_ratio", text="")

            layout.prop(props, "vertical_scale")
            layout.prop(props, "center_mesh")

            if obj and not props.live_preview:
                layout.operator("rtg.update_active_terrain", icon='FILE_REFRESH')


    class RTG_PT_ScaleOptions(RTG_PanelBase, bpy.types.Panel):
        """Rarely changed scale and viewport behavior."""
        bl_label = "Options"
        bl_idname = "RTG_PT_ScaleOptions"
        bl_parent_id = "RTG_PT_ScaleGeometry"
        bl_options = {'DEFAULT_CLOSED'}

        def draw(self, context):
            layout = self.layout
            props = context.scene.rtg_properties
            col = layout.column()
            col.prop(props, "apply_mesh_scale")
            col.prop(props, "auto_adjust_clipping")
            col.prop(props, "frame_terrain")


    class RTG_PT_Vegetation(RTG_PanelBase, bpy.types.Panel):
        """Topographic, hydrological and ecological masks."""
        bl_label = "Terrain Masks"
        bl_idname = "RTG_PT_Vegetation"
        bl_parent_id = "RTG_PT_MainPanel"

        def draw_header(self, context):
            self.layout.prop(context.scene.rtg_properties, "generate_plant_masks", text="")

        def draw(self, context):
            layout = self.layout
            props = context.scene.rtg_properties
            layout.enabled = props.generate_plant_masks
            obj = get_active_terrain(context)

            if obj:
                self.draw_mask_viewer(layout, obj)

            col = layout.column(align=True)
            col.label(text="Vegetation & Snow", icon='OUTLINER_OB_CURVES')
            row = col.row(align=True)
            row.prop(props, "altitude_reference", expand=True)
            if props.altitude_reference == "ABSOLUTE":
                row = col.row(align=True)
                row.prop(props, "treeline_m")
                row.operator("rtg.estimate_climate_lines", text="", icon='WORLD')
                col.prop(props, "snowline_m")
            else:
                col.prop(props, "plant_treeline_ratio")
                col.prop(props, "snow_line_ratio")

            col = layout.column(align=True)
            col.prop(props, "plant_max_slope_deg")
            col.prop(props, "plant_valley_boost")
            col.prop(props, "plant_density_threshold")

            layout.prop(props, "masks_as_vertex_groups")
            layout.prop(props, "generate_color_attribute")
            if props.generate_color_attribute:
                sub = layout.row()
                sub.scale_y = 0.7
                sub.label(text="R Water G Snow B Scree A Wet")

            if obj and not props.live_preview:
                layout.operator("rtg.recalculate_masks", icon='FILE_REFRESH')

        @staticmethod
        def draw_mask_viewer(layout, obj):
            if obj.get("rtg_mask_view"):
                active_name = obj["rtg_mask_view"]
            elif obj.mode == 'WEIGHT_PAINT' and obj.vertex_groups.active:
                active_name = obj.vertex_groups.active.name
            else:
                active_name = ""

            row = layout.row(align=True)
            row.label(text="View in Viewport", icon='HIDE_OFF')
            if active_name:
                row.operator("rtg.exit_mask_view", text="Exit", icon='OBJECT_DATAMODE')

            grid = layout.grid_flow(columns=3, even_columns=True, align=True)
            for name, label, icon in MASK_BUTTONS:
                op = grid.operator("rtg.set_active_mask", text=label, icon=icon, depress=(name == active_name))
                op.mask_name = name
            layout.separator()


    class RTG_PT_Geology(RTG_PanelBase, bpy.types.Panel):
        """Rock types from geologic maps and the procedural terrain material."""
        bl_label = "Geology & Material"
        bl_idname = "RTG_PT_Geology"
        bl_parent_id = "RTG_PT_MainPanel"

        def draw(self, context):
            layout = self.layout
            props = context.scene.rtg_properties
            obj = get_active_terrain(context)

            layout.row().prop(props, "geology_source", expand=True)
            layout.prop(props, "default_rock", text="Rock" if props.geology_source == "NONE" else "Fallback")

            if obj:
                self.draw_geology_summary(layout, obj)
                layout.operator("rtg.update_geology", icon='FILE_REFRESH')

            layout.separator()
            layout.prop(props, "create_material")
            sub = layout.column()
            sub.enabled = props.create_material
            sub.row().prop(props, "material_displacement", expand=True)
            sub.row().prop(props, "material_quality", expand=True)
            surface = get_surface_node(obj) if obj else None
            if surface:
                col = sub.column(align=True)
                for name, *_ in SURFACE_CONTROLS:
                    col.prop(surface.inputs[name], "default_value", text=name)

            layout.separator()
            layout.prop(props, "terrain_detail")
            mod = get_detail_modifier(obj) if obj else None
            if mod and mod.node_group:
                col = layout.column(align=True)
                for label, key in detail_inputs(mod):
                    col.prop(mod, f'["{key}"]', text=label)

            if obj:
                layout.separator()
                layout.row().prop(props, "water_mode", expand=True)
                if props.water_mode == "RIVER":
                    col = layout.column(align=True)
                    col.prop(props, "river_discharge_m3s")
                    col.prop(props, "river_min_area_km2")
                    col.prop(props, "river_roughness")
                    col.prop(props, "river_through_flow")
                row = layout.row(align=True)
                row.prop(props, "water_level_m")
                row.operator("rtg.estimate_water_level", text="", icon='EYEDROPPER')
                layout.operator("rtg.add_water", icon='MOD_OCEAN')
                layout.prop(props, "water_volume")

                layout.separator()
                self.draw_rocks(layout, obj)
                water_node = get_water_node(find_water(obj))
                if water_node:
                    col = layout.column(align=True)
                    for name, *_ in WATER_CONTROLS:
                        col.prop(water_node.inputs[name], "default_value", text=name)
                    row = layout.row(align=True)
                    for name, _ in WATER_COLORS:
                        row.prop(water_node.inputs[name], "default_value", text="")

                if props.scale_mode != "REAL":
                    info = layout.column(align=True)
                    info.scale_y = 0.8
                    info.label(text="Plant scattering expects", icon='INFO')
                    info.label(text="Real scale (1 unit = 1 m)")

        @staticmethod
        def draw_rocks(layout, obj):
            rocks = find_rocks(obj)
            row = layout.row(align=True)
            row.operator("rtg.scatter_rocks", icon='MESH_ICOSPHERE', text="Update Rocks" if rocks else "Scatter Rocks")
            if rocks:
                row.operator("rtg.remove_rocks", text="", icon='TRASH')
                mod = rocks.modifiers.get(SCATTER_MODIFIER)
                if mod and mod.node_group:
                    col = layout.column(align=True)
                    for label, key in scatter_inputs(mod):
                        col.prop(mod, f'["{key}"]', text=label)
                surface = get_rock_surface_node()
                if surface:
                    col = layout.column(align=True)
                    for name, *_ in ROCK_CONTROLS:
                        col.prop(surface.inputs[name], "default_value", text=name)

        @staticmethod
        def draw_geology_summary(layout, obj):
            col = layout.column(align=True)
            col.scale_y = 0.8
            units = json.loads(obj.get("rtg_geology_units", "[]"))
            for unit in sorted(units, key=lambda u: -u["share"])[:5]:
                col.label(text=f"{unit['share'] * 100:3.0f}%  {unit['label']}", icon='OUTLINER_DATA_LIGHTPROBE')
            legend = json.loads(obj.get("rtg_rock_legend", "[]"))
            if legend:
                rocks = ", ".join(f"{profile_for(k).label} {share * 100:.0f}%" for k, share in sorted(legend, key=lambda r: -r[1])[:4])
                col.label(text=rocks, icon='MESH_ICOSPHERE')
            if obj.get("rtg_geology_attribution"):
                col.label(text="Macrostrat (CC-BY 4.0)", icon='COPY_ID')


    class RTG_PT_Scene(RTG_PanelBase, bpy.types.Panel):
        """Sky, sun, haze, camera and render settings for a realistic render."""
        bl_label = "Scene & Render"
        bl_idname = "RTG_PT_Scene"
        bl_parent_id = "RTG_PT_MainPanel"
        bl_options = {'DEFAULT_CLOSED'}

        def draw(self, context):
            layout = self.layout
            props = context.scene.rtg_properties

            col = layout.column(align=True)
            col.prop(props, "sun_elevation_deg")
            col.prop(props, "sun_azimuth_deg")
            row = layout.row(align=True)
            row.prop(props, "sun_day")
            row.prop(props, "sun_hour")
            row.operator("rtg.sun_from_time", text="", icon='TIME')
            layout.prop(props, "sky_aerosols")

            layout.separator()
            layout.prop(props, "aerial_perspective")
            sub = layout.column()
            sub.enabled = props.aerial_perspective
            sub.prop(props, "haze_visibility_km")
            layout.prop(props, "place_camera")
            layout.row().prop(props, "render_preset", expand=True)
            layout.operator("rtg.setup_scene", icon='WORLD')


    PANEL_CLASSES = (
        RTG_PT_MainPanel,
        RTG_PT_DataFetch,
        RTG_PT_ScaleGeometry,
        RTG_PT_ScaleOptions,
        RTG_PT_Vegetation,
        RTG_PT_Geology,
        RTG_PT_Scene,
    )
else:
    PANEL_CLASSES = ()
