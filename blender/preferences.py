"""Add-on preferences for Real Terrain Generator (API Keys & Cache Settings)."""

from __future__ import annotations
import os
import tempfile
from pathlib import Path
from typing import Optional

try:
    import bpy
    from bpy.props import StringProperty, EnumProperty
    BLENDER_AVAILABLE = True
except ImportError:
    BLENDER_AVAILABLE = False


if BLENDER_AVAILABLE:
    class RTG_AddonPreferences(bpy.types.AddonPreferences):
        """Add-on preferences for persistent API Keys and storage paths."""
        bl_idname = __package__.partition(".")[0] if __package__ else "real_terrain_generator"

        opentopography_api_key: StringProperty(
            name="OpenTopography API Key",
            description="Personal API key obtained from https://portal.opentopography.org/myopentopo",
            subtype='PASSWORD',
            default="",
        )

        default_opentopo_dataset: EnumProperty(
            name="Default OpenTopo Dataset",
            description="Default dataset to request when using OpenTopography",
            items=[
                ("COP30", "Copernicus DEM GLO-30 (30m, Recommended)", "Global 30m DSM by ESA"),
                ("AW3D30", "ALOS World 3D (30m)", "Global 30m DSM by JAXA"),
                ("SRTMGL1", "NASA SRTM 1 arc-second (30m)", "NASA Shuttle Radar Topography Mission"),
                ("SRTMGL3", "NASA SRTM 3 arc-second (90m)", "NASA 90m global coverage"),
                ("NASADEM", "NASADEM Global (30m)", "Reprocessed SRTM with ASTER GDEM void fill"),
            ],
            default="COP30",
        )

        mapbox_access_token: StringProperty(
            name="Mapbox Access Token",
            description="Mapbox public or secret access token",
            subtype='PASSWORD',
            default="",
        )

        cache_directory: StringProperty(
            name="DEM Cache Directory",
            description="Local directory for storing downloaded DEM tiles and heightmaps",
            subtype='DIR_PATH',
            default=str(Path(tempfile.gettempdir()) / "real_terrain_generator_cache"),
        )

        def draw(self, context):
            layout = self.layout

            # OpenTopography Section
            box_topo = layout.box()
            box_topo.label(text="OpenTopography Settings (Option B)", icon='WORLD_DATA')
            
            row_key = box_topo.row(align=True)
            row_key.prop(self, "opentopography_api_key")
            
            row_info = box_topo.row(align=True)
            row_info.operator("wm.url_open", text="Get Free API Key (Portal)", icon='URL').url = "https://portal.opentopography.org/myopentopo"
            row_info.operator("wm.url_open", text="OpenTopography Docs", icon='HELP').url = "https://portal.opentopography.org/apidocs/"

            box_topo.prop(self, "default_opentopo_dataset")

            # AWS Terrarium Section
            box_aws = layout.box()
            box_aws.label(text="AWS Terrain Tiles (Option A - Default)", icon='CHECKMARK')
            box_aws.label(text="✓ 100% Free Public AWS S3 - No API Key or Account Required")

            # Cache Directory Section
            box_cache = layout.box()
            box_cache.label(text="Cache & Storage", icon='FILE_FOLDER')
            box_cache.prop(self, "cache_directory")


    def get_addon_preferences(context: Optional[bpy.types.Context] = None) -> Optional[RTG_AddonPreferences]:
        """Safely retrieve add-on preferences instance."""
        if context is None:
            context = bpy.context
        
        # Try finding the addon name in preferences
        pkg_name = __package__.partition(".")[0] if __package__ else "real_terrain_generator"
        addons = context.preferences.addons
        if pkg_name in addons and hasattr(addons[pkg_name], "preferences"):
            return addons[pkg_name].preferences
        
        # Fallback check
        for name, addon in addons.items():
            if "terrain" in name.lower() and hasattr(addon, "preferences"):
                return addon.preferences
        return None


    def get_opentopography_key(context: bpy.types.Context) -> str:
        """Get the OpenTopography API key from preferences or scene override."""
        props = context.scene.rtg_properties
        # 1. Check Scene property override
        if props.opentopography_api_key.strip():
            return props.opentopography_api_key.strip()

        # 2. Check Add-on Preferences
        prefs = get_addon_preferences(context)
        if prefs and prefs.opentopography_api_key.strip():
            return prefs.opentopography_api_key.strip()

        # 3. Check environment variable
        env_key = os.environ.get("OPENTOPOGRAPHY_API_KEY", "").strip()
        if env_key:
            return env_key

        return ""

else:
    class RTG_AddonPreferences:
        pass

    def get_addon_preferences(context=None):
        return None

    def get_opentopography_key(context):
        return ""
