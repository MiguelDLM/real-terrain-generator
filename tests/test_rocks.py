"""Unit tests for procedural rock meshes."""

import numpy as np
import pytest
from core.rocks import ROCK_SHAPES, make_rock, rock_library, icosphere


def _directed_edges(faces):
    return [(f[i], f[(i + 1) % len(f)]) for f in faces for i in range(len(f))]


@pytest.mark.parametrize("shape", ROCK_SHAPES)
def test_rocks_are_closed_unit_sized_and_outward(shape):
    verts, faces = make_rock(shape, seed=3)
    edges = _directed_edges(faces)
    assert len(set(edges)) == len(edges)                      # no duplicated directed edge
    assert all((b, a) in set(edges) for a, b in edges)        # closed, consistently oriented
    extent = verts.max(axis=0) - verts.min(axis=0)
    assert extent.max() == pytest.approx(1.0, abs=1e-5)
    assert np.allclose((verts.max(axis=0) + verts.min(axis=0)) / 2.0, 0.0, atol=1e-5)
    # Outward: signed volume of the closed mesh is positive
    vol = 0.0
    for f in faces:
        for i in range(1, len(f) - 1):
            vol += np.dot(verts[f[0]], np.cross(verts[f[i]], verts[f[i + 1]])) / 6.0
    assert vol > 0.0


def test_shapes_differ_and_seed_is_deterministic():
    platy, _ = make_rock("platy", seed=1)
    rounded, _ = make_rock("rounded", seed=1)
    assert np.ptp(platy[:, 2]) < 0.35 < np.ptp(rounded[:, 2])
    a, _ = make_rock("angular", seed=7)
    b, _ = make_rock("angular", seed=7)
    c, _ = make_rock("angular", seed=8)
    assert np.array_equal(a, b) and not np.array_equal(a, c)


def test_library_order_and_icosphere():
    lib = rock_library(variants=3)
    assert [s for s, *_ in lib] == [s for s in ROCK_SHAPES for _ in range(3)]
    v, f = icosphere(2)
    assert len(f) == 320 and np.allclose(np.linalg.norm(v, axis=1), 1.0)
