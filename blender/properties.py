"""Blender UI properties and scene settings for Real Terrain Generator."""

from __future__ import annotations

try:
    import bpy
    from bpy.props import (
        StringProperty,
        FloatProperty,
        IntProperty,
        EnumProperty,
        BoolProperty,
    )
    BLENDER_AVAILABLE = True
except ImportError:
    BLENDER_AVAILABLE = False

from ..core.geology.lithology import ROCK_PROFILES


# Settings stored on each generated terrain and loaded back into the sidebar when it is selected
TERRAIN_SETTINGS = (
    "latitude", "longitude", "area_width_km", "area_height_km",
    "provider", "opentopography_dataset", "grid_resolution", "auto_zoom", "zoom_level",
    "scale_mode", "target_size_m", "scale_ratio", "vertical_scale", "center_mesh", "apply_mesh_scale",
    "generate_plant_masks", "altitude_reference", "treeline_m", "snowline_m",
    "plant_treeline_ratio", "snow_line_ratio", "plant_max_slope_deg", "plant_valley_boost",
    "plant_density_threshold", "generate_color_attribute",
    "geology_source", "default_rock", "create_material", "material_displacement",
    "masks_as_vertex_groups", "terrain_detail", "water_level_m", "water_volume",
)


if BLENDER_AVAILABLE:
    def _on_scale_prop_updated(self, context):
        """Live update of scale / vertical relief on the selected terrain."""
        if self.live_preview:
            from .operators import update_active_terrain_quick
            update_active_terrain_quick(context)

    def _on_mask_prop_updated(self, context):
        """Live update of vegetation / snow masks on the selected terrain."""
        if self.live_preview:
            from .operators import update_active_masks_quick
            update_active_masks_quick(context)

    def _on_rock_prop_updated(self, context):
        """Reassign rocks on the selected terrain (offline: uses the stored map units)."""
        if self.live_preview:
            from .operators import update_active_rocks_quick
            update_active_rocks_quick(context)

    def _on_quality_updated(self, context):
        from .materials import set_quality_everywhere
        set_quality_everywhere(self.material_quality)

    def _on_mask_storage_updated(self, context):
        from .operators import get_active_terrain, get_grid_for_object, update_object_masks
        obj = get_active_terrain(context)
        grid = get_grid_for_object(obj)
        if grid and self.generate_plant_masks:
            update_object_masks(obj, grid, self)

    def _on_detail_updated(self, context):
        from .operators import update_active_detail_quick
        update_active_detail_quick(context)

    def _on_water_volume_updated(self, context):
        from .operators import update_active_water_quick
        update_active_water_quick(context)

    def _on_displacement_updated(self, context):
        from .operators import update_active_material_quick
        update_active_material_quick(context)

    def _on_sun_updated(self, context):
        from .scene_setup import update_sky
        update_sky(context.scene, self.sun_elevation_deg, self.sun_azimuth_deg, self.sky_aerosols)

    def _on_haze_updated(self, context):
        from .operators import update_active_haze_quick
        update_active_haze_quick(context)

    ROCK_ITEMS = [(key, p.label, f"{p.rock_class.capitalize()}, weathers {p.weathering}") for key, p in ROCK_PROFILES.items()]

    class RTG_Properties(bpy.types.PropertyGroup):
        """Scene properties for Real Terrain Generator."""

        live_preview: BoolProperty(
            name="Live Update",
            description="Apply scale, relief and mask changes to the selected terrain while dragging sliders",
            default=True,
        )

        # Geocoding / Search
        location_search: StringProperty(
            name="Search Location",
            description="Landmark, mountain or city (e.g. 'Mount Everest', 'Teide', 'Grand Canyon'). Press Search to fill the coordinates",
            default="Teide, Tenerife",
        )

        search_status: StringProperty(
            name="Search Status",
            default="",
        )

        # Coordinates
        latitude: FloatProperty(
            name="Latitude",
            description="Center latitude in decimal degrees (-85 to +85)",
            default=28.2723,
            min=-85.0,
            max=85.0,
            precision=5,
        )

        longitude: FloatProperty(
            name="Longitude",
            description="Center longitude in decimal degrees (-180 to +180)",
            default=-16.6425,
            min=-180.0,
            max=180.0,
            precision=5,
        )

        # Extent / Area
        area_width_km: FloatProperty(
            name="Width",
            description="East-West span in kilometers",
            default=10.0,
            min=0.2,
            max=200.0,
            precision=2,
            step=100,
        )

        area_height_km: FloatProperty(
            name="Length",
            description="North-South span in kilometers",
            default=10.0,
            min=0.2,
            max=200.0,
            precision=2,
            step=100,
        )

        # Detail & Quality
        grid_resolution: EnumProperty(
            name="Mesh Resolution",
            description="Vertices along the longest side of the terrain (the short side keeps the area's proportions)",
            items=[
                ("128", "128 (Preview)", "Fast lightweight preview"),
                ("256", "256 (Balanced)", "Good balance between performance and detail"),
                ("512", "512 (High)", "Sharp mountain ridges and valleys"),
                ("1024", "1024 (Hero)", "Maximum geometric detail"),
            ],
            default="256",
        )

        auto_zoom: BoolProperty(
            name="Auto Detail",
            description="Pick the lowest tile zoom whose data is at least as dense as the mesh resolution",
            default=True,
        )

        zoom_level: IntProperty(
            name="Tile Zoom",
            description="Slippy map zoom level of the AWS tiles (higher = finer data, more tiles to download)",
            default=12,
            min=6,
            max=15,
        )

        provider: EnumProperty(
            name="Elevation Source",
            description="Digital elevation model provider",
            items=[
                ("AWS_TERRARIUM", "AWS Terrain Tiles", "Free global tiles, no account needed (SRTM / USGS 3DEP / ETOPO1 mosaic)"),
                ("OPENTOPOGRAPHY", "OpenTopography", "Copernicus / ALOS / SRTM 30 m datasets (free API key required)"),
            ],
            default="AWS_TERRARIUM",
        )

        opentopography_dataset: EnumProperty(
            name="Dataset",
            description="OpenTopography DEM dataset",
            items=[
                ("COP30", "Copernicus GLO-30 (30 m)", "Global 30 m DSM by ESA (recommended)"),
                ("AW3D30", "ALOS World 3D (30 m)", "Global 30 m DSM by JAXA"),
                ("SRTMGL1", "NASA SRTM (30 m)", "NASA Shuttle Radar Topography Mission 30 m"),
                ("SRTMGL3", "NASA SRTM (90 m)", "NASA Shuttle Radar Topography Mission 90 m"),
                ("NASADEM", "NASADEM (30 m)", "Reprocessed SRTM with ASTER GDEM void fill"),
            ],
            default="COP30",
        )

        # Scale
        scale_mode: EnumProperty(
            name="Scale",
            description="How real-world meters map to Blender units",
            items=[
                ("FIT", "Fit", "Scale the terrain so its longest side matches Target Size"),
                ("REAL", "Real", "1:1 real-world scale (needs large viewport clipping)"),
                ("CUSTOM", "Ratio", "Map scale ratio, e.g. 1:1000 turns 1 km into 1 m"),
            ],
            default="FIT",
            update=_on_scale_prop_updated,
        )

        target_size_m: FloatProperty(
            name="Target Size",
            description="Longest horizontal side of the terrain in the scene",
            default=50.0,
            min=0.1,
            max=100000.0,
            precision=1,
            subtype='DISTANCE',
            unit='LENGTH',
            update=_on_scale_prop_updated,
        )

        scale_ratio: FloatProperty(
            name="Ratio 1",
            description="Map scale denominator: real meters per scene meter (1000 = 1:1000)",
            default=1000.0,
            min=1.0,
            max=10000000.0,
            precision=0,
            step=10000,
            update=_on_scale_prop_updated,
        )

        vertical_scale: FloatProperty(
            name="Vertical Exaggeration",
            description="Height multiplier (1.0 = true real-world proportions)",
            default=1.0,
            min=0.01,
            max=10.0,
            precision=2,
            update=_on_scale_prop_updated,
        )

        center_mesh: BoolProperty(
            name="Center at Origin",
            description="Center the terrain at (0, 0, 0) instead of its south-west corner",
            default=True,
            update=_on_scale_prop_updated,
        )

        apply_mesh_scale: BoolProperty(
            name="Bake Scale into Mesh",
            description="Write the scale into the vertex positions (object scale stays 1). Off: the mesh keeps real meters and the object transform carries the scale",
            default=True,
            update=_on_scale_prop_updated,
        )

        auto_adjust_clipping: BoolProperty(
            name="Adjust Clipping",
            description="Increase viewport and camera Clip End so large terrains are not cut off",
            default=True,
        )

        frame_terrain: BoolProperty(
            name="Frame New Terrain",
            description="Center the 3D viewport on a newly generated terrain",
            default=True,
        )

        # Terrain masks
        generate_plant_masks: BoolProperty(
            name="Terrain Masks",
            description="Create vertex groups (RTG_Plant_Density, RTG_Water_Flow, RTG_Snow, ...) for scattering, particles and shading",
            default=True,
        )

        altitude_reference: EnumProperty(
            name="Altitude Lines",
            description="How the tree line and snow line are defined",
            items=[
                ("ABSOLUTE", "Real Altitude", "Tree and snow lines in meters above sea level (physically consistent across places)"),
                ("RELATIVE", "Relative", "Tree and snow lines as a fraction of the local relief (0 = lowest point, 1 = summit)"),
            ],
            default="ABSOLUTE",
            update=_on_mask_prop_updated,
        )

        treeline_m: FloatProperty(
            name="Tree Line",
            description="Altitude above sea level where trees stop growing (forest thins over the 300 m below it)",
            default=2000.0,
            min=-500.0,
            max=9000.0,
            precision=0,
            step=5000,
            update=_on_mask_prop_updated,
        )

        snowline_m: FloatProperty(
            name="Snow Line",
            description="Altitude above sea level where snow starts to accumulate (pole-facing slopes hold snow ~150 m lower)",
            default=3000.0,
            min=-500.0,
            max=9000.0,
            precision=0,
            step=5000,
            update=_on_mask_prop_updated,
        )

        plant_treeline_ratio: FloatProperty(
            name="Tree Line",
            description="Relative altitude (0 = lowest point, 1 = summit) above which trees stop growing",
            default=0.80,
            min=0.1,
            max=1.0,
            precision=2,
            update=_on_mask_prop_updated,
        )

        snow_line_ratio: FloatProperty(
            name="Snow Line",
            description="Relative altitude (0 = lowest point, 1 = summit) above which snow accumulates",
            default=0.65,
            min=0.05,
            max=1.0,
            precision=2,
            update=_on_mask_prop_updated,
        )

        plant_max_slope_deg: FloatProperty(
            name="Max Slope",
            description="Steepest slope where vegetation grows; steeper cliffs get zero density",
            default=35.0,
            min=5.0,
            max=85.0,
            precision=1,
            update=_on_mask_prop_updated,
        )

        plant_valley_boost: FloatProperty(
            name="Valley Boost",
            description="Extra plant density in sheltered hollows and valley floors",
            default=0.30,
            min=0.0,
            max=1.0,
            precision=2,
            update=_on_mask_prop_updated,
        )

        plant_density_threshold: FloatProperty(
            name="Cutoff",
            description="Densities below this value are removed from the RTG_Plant_Density group",
            default=0.03,
            min=0.0,
            max=0.5,
            precision=3,
            update=_on_mask_prop_updated,
        )

        # Geology and material
        geology_source: EnumProperty(
            name="Geology",
            description="Where the rock types come from",
            items=[
                ("MACROSTRAT", "Geologic Map", "Real map units from Macrostrat (USGS, IGME, BGS... compiled); detail depends on the country"),
                ("NONE", "Single Rock", "One rock type for the whole terrain"),
            ],
            default="MACROSTRAT",
        )

        default_rock: EnumProperty(
            name="Rock",
            description="Rock used with Single Rock, and where the map has no recognizable lithology",
            items=ROCK_ITEMS,
            default="volcanic",
            update=_on_rock_prop_updated,
        )

        create_material: BoolProperty(
            name="Terrain Material",
            description="Create a procedural rock, soil, vegetation and snow material driven by the terrain data",
            default=True,
        )

        material_displacement: EnumProperty(
            name="Relief",
            description="How the rock micro-relief is rendered",
            items=[
                ("BUMP", "Bump", "Shading only; fast in EEVEE and Cycles"),
                ("DISPLACEMENT", "Displacement", "True displacement with adaptive subdivision (best in Cycles close-ups; slower)"),
            ],
            default="BUMP",
            update=_on_displacement_updated,
        )

        terrain_detail: BoolProperty(
            name="Cliff Detail",
            description="Geometry Nodes modifier that sculpts buttresses, ledges and blocks on steep walls (real geometry: plants and shadows follow it)",
            default=True,
            update=_on_detail_updated,
        )

        water_level_m: FloatProperty(
            name="Water Level",
            description="Altitude of the water surface in meters above sea level",
            default=0.0,
            min=-500.0,
            max=9000.0,
            precision=1,
        )

        water_volume: BoolProperty(
            name="Volumetric Water",
            description="Fill the water with a physically sized volume (absorption and silt scattering by real depth). Best in Cycles; off: lighter depth shading for EEVEE",
            default=False,
            update=_on_water_volume_updated,
        )

        material_quality: EnumProperty(
            name="Quality",
            description="Texture detail of every RTG terrain material in the file",
            items=[
                ("PREVIEW", "Preview", "Fewer noise octaves: fluid viewport (switch to Final before rendering)"),
                ("FINAL", "Final", "Full texture detail for renders"),
            ],
            default="PREVIEW",
            update=_on_quality_updated,
        )

        # Scene & render (scene-wide, not stored per terrain)
        sun_elevation_deg: FloatProperty(
            name="Sun Elevation",
            description="Sun height above the horizon in degrees (low sun = long shadows and warm light)",
            default=25.0,
            min=-5.0,
            max=90.0,
            update=_on_sun_updated,
        )

        sun_azimuth_deg: FloatProperty(
            name="Sun Azimuth",
            description="Compass direction of the sun in degrees (0 north, 90 east, 180 south, 270 west)",
            default=225.0,
            min=0.0,
            max=360.0,
            update=_on_sun_updated,
        )

        sun_day: IntProperty(
            name="Day",
            description="Day of the year (1 = Jan 1, 172 = Jun 21, 355 = Dec 21) for Sun from Time",
            default=172,
            min=1,
            max=365,
        )

        sun_hour: FloatProperty(
            name="Hour",
            description="Local solar time for Sun from Time (12 = solar noon)",
            default=16.5,
            min=0.0,
            max=24.0,
            precision=2,
        )

        sky_aerosols: FloatProperty(
            name="Sky Haze",
            description="Dust and water droplets in the sky (1 = clear, 3-5 = humid or hazy, 10 = polluted)",
            default=1.5,
            min=0.0,
            max=10.0,
            update=_on_sun_updated,
        )

        aerial_perspective: BoolProperty(
            name="Aerial Perspective",
            description="Haze volume around the terrain: distant ridges fade and turn blue (real visibility at any scale; adds render time)",
            default=True,
        )

        haze_visibility_km: FloatProperty(
            name="Visibility (km)",
            description="Distance at which the haze hides a ridge (clear air 50-100 km, humid tropics 10-20 km, mist 1-5 km)",
            default=30.0,
            min=0.5,
            max=300.0,
            update=_on_haze_updated,
        )

        place_camera: BoolProperty(
            name="Place Camera",
            description="Create or move the RTG_Camera to an oblique view along the terrain with the sun to the side",
            default=True,
        )

        render_preset: EnumProperty(
            name="Render",
            description="Cycles sampling and terrain texture detail applied by Setup Scene",
            items=[
                ("PREVIEW", "Preview", "128 samples, Preview material quality: quick test renders"),
                ("FINAL", "Final", "512 samples with denoising, Final material quality"),
            ],
            default="FINAL",
        )

        masks_as_vertex_groups: BoolProperty(
            name="Vertex Groups",
            description="Store masks as vertex groups (for particle systems and weight painting). Off: lighter float attributes, read by Geometry Nodes the same way",
            default=False,
            update=_on_mask_storage_updated,
        )

        generate_color_attribute: BoolProperty(
            name="Shader Color Attribute",
            description="Pack masks into the RTG_Geo_Masks color attribute (R=Water Flow, G=Snow, B=Talus, A=Wetness) for the Shader Editor",
            default=True,
            update=_on_mask_prop_updated,
        )
else:
    class RTG_Properties:
        pass
