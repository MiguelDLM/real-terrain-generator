"""Geology: lithology catalog, Macrostrat map units, stratigraphy and rock placement."""

from .lithology import RockProfile, ROCK_PROFILES, WEATHERING_STYLES, classify_lithology, profile_for
from .macrostrat import MacrostratClient, MapUnit, GeologyMap, fetch_geology_map, polygon_mask, fill_nearest
from .stratigraphy import StratLayer, Stratigraphy, layers_from_macrostrat_units
from .rock_masks import RockLayers, assign_rocks, plan_surface_terms, SURFACE_TERMS

__all__ = [
    "RockProfile", "ROCK_PROFILES", "WEATHERING_STYLES", "classify_lithology", "profile_for",
    "MacrostratClient", "MapUnit", "GeologyMap", "fetch_geology_map", "polygon_mask", "fill_nearest",
    "StratLayer", "Stratigraphy", "layers_from_macrostrat_units",
    "RockLayers", "assign_rocks", "plan_surface_terms", "SURFACE_TERMS",
]
