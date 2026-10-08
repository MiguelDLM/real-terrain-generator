"""Add-on preferences for Real Terrain Generator (API keys and download cache)."""

from __future__ import annotations
import os
import tempfile
from pathlib import Path
from typing import Optional

try:
    import bpy
    from bpy.props import StringProperty
    BLENDER_AVAILABLE = True
except ImportError:
    BLENDER_AVAILABLE = False

DEFAULT_CACHE_DIR = Path(tempfile.gettempdir()) / "real_terrain_generator_cache"
ADDON_PACKAGE = __package__.rpartition(".")[0] if __package__ else "real_terrain_generator"


def _folder_size_mb(folder: Path) -> tuple[int, float]:
    """(file count, total MB) of a folder, without recursing."""
    count, total = 0, 0
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                if entry.is_file():
                    count += 1
                    total += entry.stat().st_size
    except OSError:
        pass
    return count, total / (1024 * 1024)


if BLENDER_AVAILABLE:
    class RTG_AddonPreferences(bpy.types.AddonPreferences):
        """Persistent API keys and cache location (kept out of .blend files)."""
        bl_idname = ADDON_PACKAGE

        opentopography_api_key: StringProperty(
            name="OpenTopography API Key",
            description="Personal API key from https://portal.opentopography.org/myopentopo (stored in your preferences, never in .blend files)",
            subtype='PASSWORD',
            default="",
        )

        cache_directory: StringProperty(
            name="Download Cache",
            description="Folder for downloaded elevation tiles, so the same area is not downloaded twice",
            subtype='DIR_PATH',
            default=str(DEFAULT_CACHE_DIR),
        )

        def draw(self, context):
            layout = self.layout

            box = layout.box()
            box.label(text="OpenTopography (optional)", icon='WORLD_DATA')
            box.prop(self, "opentopography_api_key")
            row = box.row(align=True)
            row.operator("wm.url_open", text="Get Free API Key", icon='URL').url = "https://portal.opentopography.org/myopentopo"
            row.operator("wm.url_open", text="API Docs", icon='HELP').url = "https://portal.opentopography.org/apidocs/"
            box.label(text="The OPENTOPOGRAPHY_API_KEY environment variable also works.", icon='INFO')

            box = layout.box()
            box.label(text="Download Cache", icon='FILE_FOLDER')
            box.prop(self, "cache_directory", text="")
            count, size_mb = _folder_size_mb(get_cache_directory(context))
            row = box.row()
            row.label(text=f"{count} files, {size_mb:.1f} MB")
            row.operator("rtg.clear_cache", icon='TRASH')
            box.label(text="Generated terrains keep their elevation inside the .blend file.", icon='INFO')


    def get_addon_preferences(context: Optional[bpy.types.Context] = None) -> Optional[RTG_AddonPreferences]:
        """Add-on preferences instance, or None when the add-on is not enabled (e.g. scripted use)."""
        context = context or bpy.context
        addon = context.preferences.addons.get(ADDON_PACKAGE)
        return addon.preferences if addon else None


    def get_cache_directory(context: Optional[bpy.types.Context] = None) -> Path:
        prefs = get_addon_preferences(context)
        if prefs and prefs.cache_directory.strip():
            return Path(bpy.path.abspath(prefs.cache_directory.strip()))
        return DEFAULT_CACHE_DIR


    def get_opentopography_key(context: bpy.types.Context) -> str:
        """OpenTopography API key from the add-on preferences, or the OPENTOPOGRAPHY_API_KEY environment variable."""
        prefs = get_addon_preferences(context)
        if prefs and prefs.opentopography_api_key.strip():
            return prefs.opentopography_api_key.strip()
        return os.environ.get("OPENTOPOGRAPHY_API_KEY", "").strip()

else:
    class RTG_AddonPreferences:
        pass

    def get_addon_preferences(context=None):
        return None

    def get_cache_directory(context=None):
        return DEFAULT_CACHE_DIR

    def get_opentopography_key(context):
        return ""
