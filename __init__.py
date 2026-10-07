"""
Real Terrain Generator - Blender Extension
Generate accurate 3D terrains and 16-bit heightmaps from real-world elevation data (DEM)
with botanical masks designed to drive procedural vegetation and plant distribution.
"""

bl_info = {
    "name": "Real Terrain Generator",
    "author": "Miguel Diaz de Leon-Munoz",
    "version": (0, 1, 0),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > Real Terrain",
    "description": "Generate 3D terrains from real-world elevation data (AWS Terrain Tiles DEM) with botanical masks",
    "category": "Add Mesh",
}

try:
    import bpy
    from bpy.props import PointerProperty
    BLENDER_AVAILABLE = True
except ImportError:
    BLENDER_AVAILABLE = False

if BLENDER_AVAILABLE:
    from .blender.preferences import RTG_AddonPreferences
    from .blender.properties import RTG_Properties
    from .blender.operators import OPERATOR_CLASSES
    from .blender.panel import PANEL_CLASSES

    classes = (RTG_AddonPreferences, RTG_Properties) + OPERATOR_CLASSES + PANEL_CLASSES

    def register():
        for cls in classes:
            bpy.utils.register_class(cls)
        bpy.types.Scene.rtg_properties = PointerProperty(type=RTG_Properties)

    def unregister():
        if hasattr(bpy.types.Scene, "rtg_properties"):
            del bpy.types.Scene.rtg_properties
        for cls in reversed(classes):
            bpy.utils.unregister_class(cls)

else:
    def register():
        pass

    def unregister():
        pass


if __name__ == "__main__":
    register()
