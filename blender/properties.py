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


if BLENDER_AVAILABLE:
    class RTG_Properties(bpy.types.PropertyGroup):
        """Scene properties for Real Terrain Generator."""

        # Geocoding / Search
        location_search: StringProperty(
            name="Search Location",
            description="Type a landmark, mountain or city (e.g., 'Mount Everest', 'Teide', 'Grand Canyon')",
            default="Teide, Tenerife",
        )

        search_status: StringProperty(
            name="Search Status",
            default="",
        )

        # Coordinates
        latitude: FloatProperty(
            name="Latitude",
            description="Latitude in decimal degrees (-85 to +85)",
            default=28.2723,
            min=-85.0,
            max=85.0,
            precision=5,
        )

        longitude: FloatProperty(
            name="Longitude",
            description="Longitude in decimal degrees (-180 to +180)",
            default=-16.6425,
            min=-180.0,
            max=180.0,
            precision=5,
        )

        # Extent / Area
        area_width_km: FloatProperty(
            name="Width (km)",
            description="East-West span in kilometers",
            default=10.0,
            min=0.2,
            max=200.0,
            precision=2,
            step=100,
        )

        area_height_km: FloatProperty(
            name="Length (km)",
            description="North-South span in kilometers",
            default=10.0,
            min=0.2,
            max=200.0,
            precision=2,
            step=100,
        )

        # Detail & Quality
        zoom_level: IntProperty(
            name="Tile Zoom",
            description="Slippy map zoom level (higher = more terrain details, more tiles)",
            default=12,
            min=6,
            max=15,
        )

        grid_resolution: EnumProperty(
            name="Mesh Resolution",
            description="Resolution of the generated elevation grid / mesh",
            items=[
                ("128", "128 x 128 (Fast Preview)", "Fast lightweight preview"),
                ("256", "256 x 256 (Balanced)", "Good balance between performance and detail"),
                ("512", "512 x 512 (High Quality)", "Sharp mountain ridges and valleys"),
                ("1024", "1024 x 1024 (Cinematic / Hero)", "Maximum geometric detail"),
            ],
            default="256",
        )

        vertical_scale: FloatProperty(
            name="Vertical Scale (Z)",
            description="Vertical exaggeration multiplier (1.0 = real-world metric proportion)",
            default=1.0,
            min=0.01,
            max=10.0,
            precision=2,
        )

        center_mesh: BoolProperty(
            name="Center at Origin",
            description="Center the generated terrain at (0, 0, 0)",
            default=True,
        )

        # Mode
        generation_mode: EnumProperty(
            name="Geometry Mode",
            description="How to construct the 3D terrain in Blender",
            items=[
                ("BMESH", "Direct Metric 3D Mesh", "Generates permanent editable geometry in metric scale"),
                ("DISPLACE", "Displace Modifier + Plane", "Flat plane with Subdivision + Displace modifier using 16-bit heightmap"),
            ],
            default="BMESH",
        )

        # Providers
        provider: EnumProperty(
            name="DEM Source",
            description="Elevation data source",
            items=[
                ("AWS_TERRARIUM", "AWS Terrain Tiles (Free)", "Zero API keys needed, public AWS S3 bucket"),
                ("OPENTOPOGRAPHY", "OpenTopography (API Key)", "Copernicus 30m / ALOS / SRTM / LiDAR"),
                ("MAPBOX", "Mapbox Terrain-RGB (Token)", "Global Web Mercator elevation tiles"),
                ("USGS_3DEP", "USGS 3DEP (US Only)", "1m - 10m LiDAR DEMs in the United States"),
            ],
            default="AWS_TERRARIUM",
        )

        opentopography_api_key: StringProperty(
            name="OpenTopography Key (Override)",
            description="API Key override (leave empty to use key from Preferences)",
            subtype='PASSWORD',
            default="",
        )

        opentopography_dataset: EnumProperty(
            name="Dataset",
            description="OpenTopography DEM Dataset",
            items=[
                ("COP30", "Copernicus GLO-30 (30m, Recommended)", "Global 30m DSM by ESA"),
                ("AW3D30", "ALOS World 3D (30m)", "Global 30m DSM by JAXA"),
                ("SRTMGL1", "NASA SRTM (30m)", "NASA Shuttle Radar Topography Mission 30m"),
                ("SRTMGL3", "NASA SRTM (90m)", "NASA Shuttle Radar Topography Mission 90m"),
                ("NASADEM", "NASADEM Global (30m)", "Reprocessed SRTM with ASTER GDEM void fill"),
            ],
            default="COP30",
        )

        # Integration with Procedural Plant Generator
        generate_plant_masks: BoolProperty(
            name="Generate Botanical Masks",
            description="Create vertex groups (RTG_Elevation, RTG_Slope, RTG_Plant_Density) for Procedural Plant Generator",
            default=True,
        )

        save_heightmap_file: BoolProperty(
            name="Save 16-bit Heightmap",
            description="Save a high-precision 16-bit Grayscale PNG heightmap image file",
            default=True,
        )
else:
    class RTG_Properties:
        pass
