"""Unit tests for lithology classification, Macrostrat mapping, stratigraphy and rock placement."""

import numpy as np
import pytest
from core.geo import BoundingBox
from core.dem_sources.base import ElevationGrid
from core.mesh_builder import analyze_terrain
from core.geology import (
    ROCK_PROFILES, WEATHERING_STYLES, classify_lithology, polygon_mask, fill_nearest,
    fetch_geology_map, StratLayer, Stratigraphy, layers_from_macrostrat_units, assign_rocks,
)
from core.geology.macrostrat import MacrostratClient

BBOX = BoundingBox(min_lat=28.0, min_lon=-17.0, max_lat=28.1, max_lon=-16.9)


def test_profiles_are_consistent():
    for p in ROCK_PROFILES.values():
        assert p.weathering in WEATHERING_STYLES
        assert 0.0 <= p.hardness <= 1.0 and 0.0 <= p.roughness <= 1.0
        assert all(0.0 <= c <= 1.0 for c in p.color + p.color_alt)


@pytest.mark.parametrize("text, first", [
    ("Edificio Teide Pico Viejo. Lavas and basaltic pyroclasts, trachytic and phonolytic", "basalt"),
    ("Edificio Cañadas. Lavas and phonolytic, basaltic, and trachy-basaltic", "basalt"),
    ("Permian sandstone Major:{sandstone}, Minor{conglomerate,limestone,gypsum}", "sandstone"),
    ("Middle Triassic limestone", "limestone"),
    ("Granite, granitic pegmatite, and aplite", "granite"),
    ("Mesozoic sedimentary rocks", "sandstone"),
    ("Calizas y margas", "limestone"),
])
def test_classify_lithology(text, first):
    rocks = classify_lithology(text)
    assert rocks[0][0] == first
    assert pytest.approx(sum(w for _, w in rocks)) == 1.0


def test_classify_lithology_matches_word_starts_only():
    assert classify_lithology("still water") == []
    assert [k for k, _ in classify_lithology("Lavas and phonolytic, basaltic")] == ["basalt", "phonolite"]


def test_polygon_mask_with_hole():
    lons = np.linspace(0.0, 10.0, 11)
    lats = np.linspace(10.0, 0.0, 11)
    outer = [[2, 2], [8, 2], [8, 8], [2, 8], [2, 2]]
    hole = [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]]
    mask = polygon_mask({"type": "Polygon", "coordinates": [outer, hole]}, lons, lats)
    assert mask[lats.tolist().index(3.0), 3]          # inside ring
    assert not mask[lats.tolist().index(5.0), 5]      # inside hole
    assert not mask[0, 0]


def test_fill_nearest():
    idx = np.full((5, 5), -1)
    idx[0, 0] = 1
    idx[4, 4] = 2
    out = fill_nearest(idx)
    assert np.all(out >= 1) and out[0, 1] == 1 and out[4, 3] == 2


class FakeClient(MacrostratClient):
    """Two units split at longitude -16.95: west basalt, east pyroclasts."""

    def __init__(self):
        super().__init__(cache_dir=None)
        self.calls = 0

    def lithology_definitions(self):
        return {}

    def query_point(self, lat, lon, scales=("large",)):
        self.calls += 1
        west = lon < -16.95
        x0, x1 = (-17.0, -16.95) if west else (-16.95, -16.9)
        feature = {
            "properties": {"map_id": 1 if west else 2, "descrip": "Basaltic lavas" if west else "Pumice and lapilli",
                           "best_int_name": "Pleistocene", "source_id": 7},
            "geometry": {"type": "Polygon", "coordinates": [[[x0, 28.0], [x1, 28.0], [x1, 28.1], [x0, 28.1], [x0, 28.0]]]},
        }
        return "large", [feature], {7: "Test survey"}


def test_fetch_geology_map_adaptive():
    client = FakeClient()
    geo = fetch_geology_map(BBOX, (20, 20), client=client, lattice=4)
    assert client.calls == 2                      # one query per polygon is enough
    assert len(geo.units) == 2 and geo.queries == 2
    assert np.all(geo.unit_index >= 0)
    west = geo.units[geo.unit_index[10, 2]]
    east = geo.units[geo.unit_index[10, 17]]
    assert west.rocks[0][0] == "basalt" and east.rocks[0][0] == "pumice"
    assert "Test survey" in geo.attribution


def _grid(elev, width_m=2000.0):
    return ElevationGrid(
        elevation=elev.astype(np.float32), bbox=BBOX, width_m=width_m, height_m=width_m,
        min_elevation_m=float(elev.min()), max_elevation_m=float(elev.max()),
        provider_name="Test", attribution="Test", metadata={},
    )


def test_stratigraphy_flat_and_dipping():
    strat = Stratigraphy(
        layers=[StratLayer("Cap", 100.0, "sandstone"), StratLayer("Soft", 50.0, "shale"), StratLayer("Base", 500.0, "limestone")],
        datum_m=1000.0,
    )
    z = np.array([1050.0, 950.0, 880.0, 500.0])
    depth = strat.structural_depth(np.zeros(4), np.zeros(4), z)
    assert list(strat.layer_at_depth(depth)) == [0, 0, 1, 2]

    # Dipping 45° to the east: the same altitude cuts deeper layers to the west
    strat.dip_deg, strat.dip_azimuth_deg = 45.0, 90.0
    d_west = strat.structural_depth(np.array([-200.0]), np.zeros(1), np.array([1000.0]))
    d_east = strat.structural_depth(np.array([200.0]), np.zeros(1), np.array([1000.0]))
    assert d_west[0] == pytest.approx(200.0) and d_east[0] == pytest.approx(-200.0)


def test_layers_from_macrostrat_units():
    units = [
        {"unit_name": "Kaibab", "t_age": 270, "min_thick": 80, "max_thick": 120, "lith": [{"name": "limestone", "prop": 0.8}, {"name": "sandstone", "prop": 0.2}]},
        {"unit_name": "Thin", "t_age": 275, "min_thick": 0, "max_thick": 2, "lith": [{"name": "shale", "prop": 1}]},
        {"unit_name": "Moenkopi", "t_age": 245, "min_thick": 0, "max_thick": 300, "lith": [{"name": "mudstone", "prop": 1}]},
    ]
    layers = layers_from_macrostrat_units(units)
    assert [l.name for l in layers] == ["Moenkopi", "Kaibab"]
    assert layers[1].rock == "limestone" and layers[1].thickness_m == 100.0


def test_assign_rocks_differential_erosion_and_boulders():
    # Valley with a steep western wall and a gentle eastern slope
    x = np.linspace(-1.0, 1.0, 48)[None, :].repeat(48, axis=0)
    elev = np.where(x < 0, 1500.0 * -x, 150.0 * x) + 500.0
    grid = _grid(elev)
    analysis = analyze_terrain(grid)

    client = FakeClient()
    client.query_point = lambda lat, lon, scales=("large",): ("large", [{
        "properties": {"map_id": 1, "descrip": "Basalt lavas and pumice", "source_id": 1},
        "geometry": {"type": "Polygon", "coordinates": [[[-17.0, 28.0], [-16.9, 28.0], [-16.9, 28.1], [-17.0, 28.1], [-17.0, 28.0]]]},
    }], {})
    geo = fetch_geology_map(BBOX, elev.shape, client=client)
    rocks = assign_rocks(grid, analysis, geology=geo)

    names = np.array(rocks.rock_keys)[rocks.rock_id].reshape(elev.shape)
    steep_wall = names[:, 5:20]
    gentle = names[:, 30:45]
    assert (steep_wall == "basalt").mean() > (gentle == "basalt").mean()
    assert (gentle == "pumice").mean() > 0.5
    for arr in (rocks.boulder_density, rocks.clast_rounding, rocks.hardness):
        assert arr.shape == (elev.size,) and np.all((arr >= 0) & (arr <= 1))
    assert rocks.color.shape == (elev.size, 3)


def test_assign_rocks_with_stratigraphy_and_default():
    elev = np.tile(np.linspace(400.0, 1000.0, 32)[:, None], (1, 32))
    grid = _grid(elev)
    analysis = analyze_terrain(grid)
    strat = Stratigraphy([StratLayer("Top", 300.0, "sandstone", color=(1.0, 0.0, 0.0)), StratLayer("Bottom", 1000.0, "shale")], datum_m=1000.0)
    rocks = assign_rocks(grid, analysis, stratigraphy=strat)
    assert set(rocks.rock_keys) == {"sandstone", "shale"}
    high = rocks.unit_id.reshape(elev.shape)[elev > 750.0]
    assert np.all(high == 0)
    assert np.allclose(rocks.color.reshape(32, 32, 3)[elev > 750.0], (1.0, 0.0, 0.0))

    plain = assign_rocks(grid, analysis, default_rock="granite")
    assert plain.rock_keys == ["granite"] and np.all(plain.rock_id == 0)


def test_rock_style_parameters_and_shader_arrays():
    from core.geology.lithology import rock_style_parameters
    fracture, surface, joint, bedding = rock_style_parameters(ROCK_PROFILES["basalt"])
    assert fracture[1] == 1.0 and surface[3] == ROCK_PROFILES["basalt"].roughness and bedding == joint
    fracture, _, _, bedding = rock_style_parameters(ROCK_PROFILES["limestone"])
    assert fracture[2] >= 0.6 and bedding == ROCK_PROFILES["limestone"].bedding_m

    elev = np.random.default_rng(3).random((16, 16)) * 50.0 + 100.0
    rocks = assign_rocks(_grid(elev), analyze_terrain(_grid(elev)), default_rock="limestone")
    assert rocks.fracture.shape == (256, 4) and rocks.surface.shape == (256, 4)
    assert np.allclose(rocks.strata_depth_m, -elev.ravel())
    assert np.all(rocks.joint_m >= 0.2) and np.all(rocks.bedding_m > 0)


def test_plan_surface_terms_prunes_unused_rock_styles():
    from core.geology import plan_surface_terms
    elev = np.random.default_rng(4).random((16, 16)) * 50.0 + 100.0
    grid = _grid(elev)
    analysis = analyze_terrain(grid)
    limestone = plan_surface_terms(assign_rocks(grid, analysis, default_rock="limestone"))
    assert set(limestone) == {"blocky", "layered", "karst"}
    basalt = plan_surface_terms(assign_rocks(grid, analysis, default_rock="basalt"))
    assert "columnar" in basalt and "karst" not in basalt
