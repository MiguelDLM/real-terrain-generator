"""Procedural loose-rock meshes (boulders, cobbles, slabs, prisms) in pure NumPy.

Shapes follow how rocks break and wear:

* ``angular``: convex blocks bounded by fracture planes (scree, jointed blocks below cliffs);
* ``rounded``: worn, slightly flattened cobbles and boulders (stream beds, corestones);
* ``platy``: thin slabs split along bedding or cleavage (limestone flags, shale, slate);
* ``columnar``: hexagonal prisms from cooling joints (basalt).

Every rock is a closed mesh centred at the origin whose largest dimension is 1 (meter), so
an instance scale equals the real clast size.
"""

from __future__ import annotations
import math
from typing import Dict, List, Tuple
import numpy as np

ROCK_SHAPES = ("angular", "rounded", "platy", "columnar")


def icosphere(subdivisions: int = 2) -> Tuple[np.ndarray, np.ndarray]:
    """Unit icosphere: (vertices (N, 3), triangles (M, 3))."""
    t = (1.0 + math.sqrt(5.0)) / 2.0
    verts: List[Tuple[float, float, float]] = [
        (-1, t, 0), (1, t, 0), (-1, -t, 0), (1, -t, 0), (0, -1, t), (0, 1, t),
        (0, -1, -t), (0, 1, -t), (t, 0, -1), (t, 0, 1), (-t, 0, -1), (-t, 0, 1),
    ]
    faces = [
        (0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11), (1, 5, 9), (5, 11, 4), (11, 10, 2),
        (10, 7, 6), (7, 1, 8), (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8), (3, 8, 9), (4, 9, 5),
        (2, 4, 11), (6, 2, 10), (8, 6, 7), (9, 8, 1),
    ]
    verts = [tuple(np.asarray(v) / np.linalg.norm(v)) for v in verts]
    for _ in range(subdivisions):
        cache: Dict[Tuple[int, int], int] = {}

        def midpoint(a: int, b: int) -> int:
            key = (min(a, b), max(a, b))
            if key not in cache:
                m = (np.asarray(verts[a]) + np.asarray(verts[b])) / 2.0
                verts.append(tuple(m / np.linalg.norm(m)))
                cache[key] = len(verts) - 1
            return cache[key]

        new_faces = []
        for a, b, c in faces:
            ab, bc, ca = midpoint(a, b), midpoint(b, c), midpoint(c, a)
            new_faces += [(a, ab, ca), (b, bc, ab), (c, ca, bc), (ab, bc, ca)]
        faces = new_faces
    return np.array(verts, dtype=np.float64), np.array(faces, dtype=np.int32)


def _surface_noise(dirs: np.ndarray, rng: np.random.Generator, octaves: int, amplitude: float) -> np.ndarray:
    """Smooth radial noise on the sphere: sums of random directional waves."""
    out = np.zeros(len(dirs))
    for o in range(octaves):
        freq = 2.0 * (2.0 ** o)
        for _ in range(3):
            v = rng.normal(size=3)
            v /= np.linalg.norm(v)
            out += np.sin(freq * dirs @ v + rng.uniform(0, 2 * math.pi)) * amplitude / (2.0 ** o)
    return out


def _normalize(verts: np.ndarray) -> np.ndarray:
    lo, hi = verts.min(axis=0), verts.max(axis=0)
    centred = verts - (lo + hi) / 2.0
    return (centred / max(1e-9, (hi - lo).max())).astype(np.float32)


def _prism(rng: np.random.Generator) -> Tuple[np.ndarray, List[List[int]]]:
    """Hexagonal column, slightly irregular, with a tilted broken top."""
    angles = np.arange(6) * (math.pi / 3.0) + rng.uniform(-0.12, 0.12, 6)
    radius = 0.5 * rng.uniform(0.85, 1.1, 6)
    ring = np.column_stack([np.cos(angles) * radius, np.sin(angles) * radius])
    height = rng.uniform(1.4, 2.2)
    tilt = rng.normal(scale=0.15, size=2)
    bottom = np.column_stack([ring, np.zeros(6)])
    top = np.column_stack([ring, height + ring @ tilt])
    verts = np.concatenate([bottom, top])
    faces = [list(range(5, -1, -1)), list(range(6, 12))]
    faces += [[i, (i + 1) % 6, (i + 1) % 6 + 6, i + 6] for i in range(6)]
    return verts, faces


def make_rock(shape: str, seed: int = 0, subdivisions: int = 2) -> Tuple[np.ndarray, List[List[int]]]:
    """One rock: (vertices (N, 3) float32, faces as vertex index lists)."""
    rng = np.random.default_rng(seed)
    if shape == "columnar":
        verts, faces = _prism(rng)
        return _normalize(verts), faces

    dirs, tris = icosphere(subdivisions)
    if shape in ("angular", "platy"):
        # Convex block: radius limited by random fracture planes (radial projection of a polytope)
        n_planes = rng.integers(8, 15)
        normals = rng.normal(size=(n_planes, 3))
        normals /= np.linalg.norm(normals, axis=1, keepdims=True)
        offsets = rng.uniform(0.55, 1.0, n_planes)
        cos = dirs @ normals.T
        with np.errstate(divide="ignore"):
            limits = np.where(cos > 0.05, offsets / np.maximum(cos, 1e-6), np.inf)
        radius = np.clip(limits.min(axis=1), 0.35, 1.4)
        radius *= 1.0 + _surface_noise(dirs, rng, 2, 0.015)
        scale = np.array([1.0, rng.uniform(0.6, 0.95), rng.uniform(0.45, 0.8)])
        if shape == "platy":
            scale[2] = rng.uniform(0.15, 0.3)
    else:  # rounded
        radius = 1.0 + _surface_noise(dirs, rng, 2, 0.06)
        scale = np.array([1.0, rng.uniform(0.7, 0.95), rng.uniform(0.45, 0.7)])
    verts = dirs * radius[:, None] * scale
    return _normalize(verts), tris.tolist()


def rock_library(variants: int = 4, subdivisions: int = 2, seed: int = 0) -> List[Tuple[str, int, np.ndarray, List[List[int]]]]:
    """All shapes x variants as (shape, variant, vertices, faces), ordered shape by shape."""
    out = []
    for s_idx, shape in enumerate(ROCK_SHAPES):
        for v in range(variants):
            verts, faces = make_rock(shape, seed=seed * 1000 + s_idx * 100 + v, subdivisions=subdivisions)
            out.append((shape, v, verts, faces))
    return out
