"""Blender operators for Real Terrain Generator."""

from __future__ import annotations
import tempfile
from pathlib import Path

try:
    import bpy
    import bmesh
    BLENDER_AVAILABLE = True
except ImportError:
    BLENDER_AVAILABLE = False

from ..core.geo import (
    bbox_from_center,
    recommend_zoom_for_area,
    haversine_distance_m,
    meters_per_pixel,
)
from ..core.geocoding import search_location
from ..core.dem_sources.aws_terrarium import AWSTerrariumProvider
from ..core.dem_sources.opentopography import OpenTopographyProvider
from ..core.dem_sources.mapbox_rgb import MapboxRGBProvider
from ..core.dem_sources.usgs_3dep import USGS3DEPProvider
from ..core.heightmap import save_heightmap_png16
from ..core.mesh_builder import build_terrain_mesh_data


if BLENDER_AVAILABLE:
    class RTG_OT_SearchLocation(bpy.types.Operator):
        """Search for a place name using OpenStreetMap Nominatim and auto-fill coordinates."""
        bl_idname = "rtg.search_location"
        bl_label = "Search Place"
        bl_options = {'REGISTER', 'UNDO'}

        def execute(self, context):
            props = context.scene.rtg_properties
            query = props.location_search.strip()
            if not query:
                self.report({'WARNING'}, "Please enter a location name to search.")
                return {'CANCELLED'}

            try:
                results = search_location(query, limit=3)
            except Exception as e:
                self.report({'ERROR'}, f"Search failed: {e}")
                return {'CANCELLED'}

            if not results:
                props.search_status = "No results found. Try another query."
                self.report({'WARNING'}, "No matching locations found.")
                return {'CANCELLED'}

            top = results[0]
            props.latitude = top.latitude
            props.longitude = top.longitude

            # Calculate span from bbox if available
            w_m = haversine_distance_m(top.latitude, top.bbox.min_lon, top.latitude, top.bbox.max_lon)
            h_m = haversine_distance_m(top.bbox.min_lat, top.longitude, top.bbox.max_lat, top.longitude)
            
            w_km = max(1.0, min(100.0, w_m / 1000.0))
            h_km = max(1.0, min(100.0, h_m / 1000.0))
            props.area_width_km = round(w_km, 1)
            props.area_height_km = round(h_km, 1)

            # Recommend balanced zoom
            props.zoom_level = recommend_zoom_for_area(props.area_width_km, props.area_height_km)
            short_name = (top.display_name[:55] + "..") if len(top.display_name) > 55 else top.display_name
            props.search_status = f"✓ {short_name}"

            self.report({'INFO'}, f"Located: {top.display_name}")
            return {'FINISHED'}


    class RTG_OT_AutoZoom(bpy.types.Operator):
        """Calculate and set the recommended zoom level for the current area dimensions."""
        bl_idname = "rtg.auto_zoom"
        bl_label = "Auto Zoom"
        bl_options = {'REGISTER', 'UNDO'}

        def execute(self, context):
            props = context.scene.rtg_properties
            props.zoom_level = recommend_zoom_for_area(props.area_width_km, props.area_height_km)
            m_px = meters_per_pixel(props.latitude, props.zoom_level)
            self.report({'INFO'}, f"Zoom set to {props.zoom_level} (~{m_px:.1f} m/pixel)")
            return {'FINISHED'}


    class RTG_OT_GenerateTerrain(bpy.types.Operator):
        """Fetch elevation data from DEM provider and generate 3D terrain in the scene."""
        bl_idname = "rtg.generate_terrain"
        bl_label = "Generate Real Terrain"
        bl_options = {'REGISTER', 'UNDO'}

        def execute(self, context):
            props = context.scene.rtg_properties

            # Select Provider
            if props.provider == "AWS_TERRARIUM":
                provider = AWSTerrariumProvider()
            elif props.provider == "OPENTOPOGRAPHY":
                if not props.opentopography_api_key.strip():
                    self.report({'ERROR'}, "OpenTopography requires an API Key. Please provide it in settings.")
                    return {'CANCELLED'}
                provider = OpenTopographyProvider(api_key=props.opentopography_api_key.strip())
            elif props.provider == "MAPBOX":
                self.report({'ERROR'}, "Mapbox provider is planned for a future update. Use AWS Terrain Tiles.")
                return {'CANCELLED'}
            elif props.provider == "USGS_3DEP":
                self.report({'ERROR'}, "USGS 3DEP is planned for a future update. Use AWS Terrain Tiles.")
                return {'CANCELLED'}
            else:
                provider = AWSTerrariumProvider()

            # Cache directory
            cache_dir = Path(tempfile.gettempdir()) / "real_terrain_generator_cache"

            # Compute Geographic Bounding Box
            bbox = bbox_from_center(
                center_lat=props.latitude,
                center_lon=props.longitude,
                width_km=props.area_width_km,
                height_km=props.area_height_km,
            )

            target_res = int(props.grid_resolution)

            # Fetch Elevation Grid
            self.report({'INFO'}, f"Fetching DEM tiles at Zoom {props.zoom_level}...")
            try:
                grid = provider.fetch_elevation_grid(
                    bbox=bbox,
                    zoom=props.zoom_level,
                    target_resolution=target_res,
                    cache_dir=cache_dir,
                )
            except Exception as e:
                self.report({'ERROR'}, f"Failed to download elevation data: {e}")
                return {'CANCELLED'}

            # Optional: Save 16-bit Heightmap image
            heightmap_path = None
            if props.save_heightmap_file:
                hmap_file = cache_dir / f"heightmap_{props.latitude:.4f}_{props.longitude:.4f}_z{props.zoom_level}.png"
                heightmap_path, min_e, max_e = save_heightmap_png16(grid.elevation, hmap_file)

            # Build Mesh
            self.report({'INFO'}, "Building 3D terrain geometry...")
            mesh_data = build_terrain_mesh_data(
                grid=grid,
                vertical_scale=props.vertical_scale,
                center_origin=props.center_mesh,
                base_elevation_zero=True,
            )

            obj_name = f"Terrain_{props.latitude:.2f}_{props.longitude:.2f}"
            mesh = bpy.data.meshes.new(f"{obj_name}_Mesh")
            
            # Load vertices and faces into Blender Mesh
            mesh.from_pydata(mesh_data.vertices.tolist(), [], mesh_data.faces.tolist())
            mesh.update()

            # Apply smooth shading
            for poly in mesh.polygons:
                poly.use_smooth = True

            # Assign UVs
            uv_layer = mesh.uv_layers.new(name="UVMap")
            # In Blender, loop UVs correspond to face corner loops
            loop_uvs = []
            for face in mesh_data.faces:
                for vert_idx in face:
                    u, v = mesh_data.uvs[vert_idx]
                    loop_uvs.append((u, v))
            
            for loop_idx, (u, v) in enumerate(loop_uvs):
                uv_layer.data[loop_idx].uv = (u, v)

            # Create Object
            obj = bpy.data.objects.new(obj_name, mesh)
            context.collection.objects.link(obj)

            # Assign Botanical Vertex Groups for Procedural Plant Generator
            if props.generate_plant_masks:
                for group_name, weights in mesh_data.vertex_weights.items():
                    vg = obj.vertex_groups.new(name=group_name)
                    for v_idx, w in enumerate(weights):
                        if w > 0.001:
                            vg.add([v_idx], float(w), 'REPLACE')

            # Select and activate object
            bpy.ops.object.select_all(action='DESELECT')
            obj.select_set(True)
            context.view_layer.objects.active = obj

            # Custom object properties for tracking
            obj["rtg_latitude"] = props.latitude
            obj["rtg_longitude"] = props.longitude
            obj["rtg_elevation_min_m"] = grid.min_elevation_m
            obj["rtg_elevation_max_m"] = grid.max_elevation_m
            obj["rtg_elevation_range_m"] = grid.elevation_range_m
            obj["rtg_provider"] = grid.provider_name
            obj["rtg_attribution"] = grid.attribution
            if heightmap_path:
                obj["rtg_heightmap_path"] = str(heightmap_path)

            self.report(
                {'INFO'},
                f"Generated terrain '{obj_name}'! Elevation: {grid.min_elevation_m:.0f}m to {grid.max_elevation_m:.0f}m (Relief: {grid.elevation_range_m:.0f}m)"
            )
            return {'FINISHED'}


    OPERATOR_CLASSES = (
        RTG_OT_SearchLocation,
        RTG_OT_AutoZoom,
        RTG_OT_GenerateTerrain,
    )
else:
    OPERATOR_CLASSES = ()
