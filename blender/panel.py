"""Blender 3D Viewport Sidebar panel for Real Terrain Generator."""

from __future__ import annotations

try:
    import bpy
    BLENDER_AVAILABLE = True
except ImportError:
    BLENDER_AVAILABLE = False

from ..core.geo import meters_per_pixel
from .preferences import get_opentopography_key


if BLENDER_AVAILABLE:
    class RTG_PT_MainPanel(bpy.types.Panel):
        """Main Sidebar Panel for Real Terrain Generator."""
        bl_label = "Real Terrain Generator"
        bl_idname = "RTG_PT_MainPanel"
        bl_space_type = 'VIEW_3D'
        bl_region_type = 'UI'
        bl_category = "Real Terrain"

        def draw(self, context):
            layout = self.layout
            props = context.scene.rtg_properties

            # --- Section 1: Location & Search ---
            box_loc = layout.box()
            box_loc.label(text="Location & Landmarks", icon='VIEW_PAN')
            
            row_search = box_loc.row(align=True)
            row_search.prop(props, "location_search", text="")
            row_search.operator("rtg.search_location", text="Search", icon='VIEWZOOM')

            if props.search_status:
                box_loc.label(text=props.search_status, icon='INFO')

            col_coords = box_loc.column(align=True)
            col_coords.prop(props, "latitude")
            col_coords.prop(props, "longitude")

            # --- Section 2: Area & Scale ---
            box_area = layout.box()
            box_area.label(text="Area & Resolution", icon='GRID')

            row_dims = box_area.row(align=True)
            row_dims.prop(props, "area_width_km")
            row_dims.prop(props, "area_height_km")

            row_zoom = box_area.row(align=True)
            row_zoom.prop(props, "zoom_level")
            row_zoom.operator("rtg.auto_zoom", text="Auto", icon='AUTO')

            # Show ground resolution estimate
            m_px = meters_per_pixel(props.latitude, props.zoom_level)
            box_area.label(text=f"Detail: ~{m_px:.1f} m / pixel at this latitude", icon='DRIVER')

            box_area.prop(props, "grid_resolution")

            # --- Section 3: Terrain Mesh & Elevation ---
            box_mesh = layout.box()
            box_mesh.label(text="Terrain Shaping", icon='MOD_DISPLACE')
            box_mesh.prop(props, "vertical_scale")
            box_mesh.prop(props, "center_mesh")
            box_mesh.prop(props, "save_heightmap_file")

            # --- Section 4: Integration with Plant Generator ---
            box_bio = layout.box()
            box_bio.label(text="Plant Gen Integration", icon='OUTLINER_OB_CURVE')
            box_bio.prop(props, "generate_plant_masks")
            if props.generate_plant_masks:
                col_sub = box_bio.column()
                col_sub.scale_y = 0.8
                col_sub.label(text="• RTG_Elevation (Altitude mask)")
                col_sub.label(text="• RTG_Slope (Cliff exclusion)")
                col_sub.label(text="• RTG_Plant_Density (Biomass distribution)")

            # --- Section 5: DEM Data Source ---
            box_source = layout.box()
            box_source.label(text="DEM Provider", icon='WORLD')
            box_source.prop(props, "provider", text="")

            if props.provider == "OPENTOPOGRAPHY":
                box_source.prop(props, "opentopography_dataset")
                api_key = get_opentopography_key(context)
                if api_key:
                    box_source.label(text="✓ API Key configured", icon='CHECKMARK')
                    box_source.prop(props, "opentopography_api_key", text="Session Key (Optional)")
                else:
                    box_source.label(text="⚠️ API Key required", icon='ERROR')
                    box_source.prop(props, "opentopography_api_key", text="API Key")

                row_actions = box_source.row(align=True)
                row_actions.operator("rtg.open_preferences", text="Settings Menu", icon='PREFERENCES')
                row_actions.operator("wm.url_open", text="Get Free Key", icon='URL').url = "https://portal.opentopography.org/myopentopo"

            elif props.provider == "AWS_TERRARIUM":
                box_source.label(text="✓ Public AWS S3 (No API Key Required)", icon='CHECKMARK')

            # --- Big Action Button ---
            layout.separator()
            row_gen = layout.row()
            row_gen.scale_y = 1.6
            row_gen.operator("rtg.generate_terrain", text="Generate Real Terrain", icon='SURFACE_DATA')

            # Attribution footer
            layout.separator()
            foot = layout.column()
            foot.scale_y = 0.7
            foot.label(text="Data: USGS / SRTM / ETOPO1 via AWS Open Data")


    PANEL_CLASSES = (RTG_PT_MainPanel,)
else:
    PANEL_CLASSES = ()
