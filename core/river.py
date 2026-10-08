"""Rivers on real terrain: drainage network, hydraulics and the flow field of the water surface.

The network is routed on a depression-filled DEM (Priority-Flood, Barnes et al. 2014), so
flow crosses DEM pits and the flat surfaces of reservoirs instead of stopping in them.
Channels start where the contributing area exceeds a threshold. Each channel cell gets:

* Discharge: the outlet discharge shared by contributing area. With *through flow*, the main
  stem (the river that crosses the whole terrain) carries the full discharge: a terrain tile
  usually shows a reach of a river whose catchment lies mostly outside it.
* Width from downstream hydraulic geometry, w = 3 Q^0.5 (Leopold & Maddock 1953).
* Depth and velocity from Manning's equation for a wide channel, Q = w d v and
  v = d^(2/3) S^(1/2) / n, with S the water-surface slope over a short reach (capped on
  cascades, where Manning no longer applies).
* Froude number Fr = v / sqrt(g d): tranquil below 1, rapids and standing waves above
  (supercritical flow), so a steep mountain torrent foams and a lowland river stays smooth.
* Whitewater from the Froude number and from cascades; foam carried downstream from it and
  fading with distance.

Around each channel the cells within its half width (plus a margin hidden under the banks)
take the channel's water surface, velocity, a parabolic depth/velocity profile, and stream
coordinates: ``s`` (meters along the flow, growing downstream and continuous across
confluences) and ``n`` (signed meters across it). The water shader textures in (s, n), so
ripples and foam streaks follow the bends of the river.
"""

from __future__ import annotations
import heapq
import math
from dataclasses import dataclass
import numpy as np

from .dem_sources.base import ElevationGrid

GRAVITY = 9.81
WIDTH_COEF = 3.0               # Hydraulic geometry: width = WIDTH_COEF * Q ** WIDTH_EXP (meters)
WIDTH_EXP = 0.5
MIN_WIDTH_M = 1.0
MIN_SLOPE = 1e-4               # Reservoirs and filled flats still drift slowly
# Manning overestimates velocity on steep step-pool reaches and cascades, where form drag
# dominates (Rickenmann 2001): the hydraulics use at most this slope; whitewater uses the real one
MAX_HYDRAULIC_SLOPE = 0.1
SLOPE_REACH_CELLS = 3          # Water-surface slope measured over this many cells downstream
FOAM_DECAY_M = 250.0           # Foam fades to 1/e this far downstream of the rapids
MIN_SURFACE_OFFSET_M = 0.05    # Water stands at least this far above the (filled) channel cell
SURFACE_SMOOTH_PASSES = 8      # Smoothing of the water-surface profile along each channel
FILL_EPSILON_M = 1e-3          # Gradient imposed across filled flats so they drain
STEM_RELIEF_M = 5.0            # Main-stem path cost grows by 1x per this many meters above the exit

_D8 = ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1))


@dataclass
class RiverParams:
    discharge_m3s: float = 20.0      # Discharge at the outlet of the main river
    min_area_km2: float = 0.5        # Contributing area where channels start
    manning_n: float = 0.035         # Bed roughness: 0.03 smooth sand bed, 0.05+ boulders
    through_flow: bool = True        # Main stem carries the full discharge across the terrain


@dataclass
class RiverField:
    """Per-cell river data on the DEM grid, (rows, cols) unless noted."""
    channel: np.ndarray        # bool: channel centerline cells
    discharge: np.ndarray      # m3/s on channel cells, 0 elsewhere
    surface_m: np.ndarray      # water-surface altitude near rivers, NaN elsewhere
    depth_m: np.ndarray        # hydraulic depth across the channel profile, 0 elsewhere
    velocity: np.ndarray       # (rows, cols, 2) m/s, x east and y north
    stream_s: np.ndarray       # meters along the flow (grows downstream)
    stream_n: np.ndarray       # signed meters across the flow
    weight: np.ndarray         # 1 inside the channel, fading to 0 just outside it
    whitewater: np.ndarray     # 0..1 rapids and cascades
    foam: np.ndarray           # 0..1 whitewater carried downstream


def priority_flood(elev: np.ndarray, epsilon: float = FILL_EPSILON_M) -> np.ndarray:
    """Fill depressions so every cell drains to the grid edge along strictly descending cells."""
    rows, cols = elev.shape
    z = elev.astype(np.float64).ravel().tolist()
    closed = [False] * (rows * cols)
    heap = []
    for r in range(rows):
        for c in (0, cols - 1) if cols > 1 else (0,):
            i = r * cols + c
            if not closed[i]:
                closed[i] = True
                heap.append((z[i], i))
    for c in range(cols):
        for r in (0, rows - 1) if rows > 1 else (0,):
            i = r * cols + c
            if not closed[i]:
                closed[i] = True
                heap.append((z[i], i))
    heapq.heapify(heap)
    while heap:
        zc, i = heapq.heappop(heap)
        r, c = divmod(i, cols)
        for dr, dc in _D8:
            nr, nc = r + dr, c + dc
            if 0 <= nr < rows and 0 <= nc < cols:
                j = nr * cols + nc
                if not closed[j]:
                    closed[j] = True
                    if z[j] <= zc:
                        z[j] = zc + epsilon
                    heapq.heappush(heap, (z[j], j))
    return np.asarray(z, dtype=np.float64).reshape(rows, cols)


def d8_receivers(z: np.ndarray, cell_x_m: float, cell_y_m: float):
    """Steepest-descent receiver (flat index, -1 at outlets) and step length of every cell."""
    rows, cols = z.shape
    rr, cc = np.indices((rows, cols))
    best = np.zeros((rows, cols))
    receiver = np.full((rows, cols), -1, dtype=np.int64)
    step = np.zeros((rows, cols))
    for dr, dc in _D8:
        nr, nc = rr + dr, cc + dc
        valid = (nr >= 0) & (nr < rows) & (nc >= 0) & (nc < cols)
        nr, nc = np.clip(nr, 0, rows - 1), np.clip(nc, 0, cols - 1)
        dist = math.hypot(dr * cell_y_m, dc * cell_x_m)
        drop = (z - z[nr, nc]) / dist
        better = valid & (drop > best)
        best[better] = drop[better]
        receiver[better] = (nr * cols + nc)[better]
        step[better] = dist
    return receiver.ravel(), step.ravel()


def _accumulate(rcv) -> list:
    """Contributing cells of every cell (itself included), in topological order."""
    acc = [1.0] * len(rcv)
    for i in _topological_order(rcv):
        r = rcv[i]
        if r >= 0:
            acc[r] += acc[i]
    return acc


def _topological_order(rcv) -> list:
    """Cells ordered so that every cell comes before its receiver (Kahn's algorithm)."""
    indegree = [0] * len(rcv)
    for r in rcv:
        if r >= 0:
            indegree[r] += 1
    stack = [i for i, d in enumerate(indegree) if d == 0]
    order = []
    while stack:
        i = stack.pop()
        order.append(i)
        r = rcv[i]
        if r >= 0:
            indegree[r] -= 1
            if indegree[r] == 0:
                stack.append(r)
    return order


def main_stem_path(z: np.ndarray, cell_x_m: float, cell_y_m: float, exit_idx: int) -> list:
    """Lowest path (flat indices) from where the main river enters the grid to its exit.

    The exit is the lowest edge cell; the entry is the lowest edge cell well away from it. The path minimizes length
    weighted by height above the exit (Dijkstra), so it follows the valley floor and crosses
    reservoirs down their middle.
    """
    rows, cols = z.shape
    zf = z.ravel().tolist()
    er, ec = divmod(exit_idx, cols)
    min_sep = 0.25 * max((cols - 1) * cell_x_m, (rows - 1) * cell_y_m)
    edge = {r * cols + c for r in range(rows) for c in (0, cols - 1)} | \
           {r * cols + c for c in range(cols) for r in (0, rows - 1)}
    far = [b for b in edge if math.hypot((b // cols - er) * cell_y_m, (b % cols - ec) * cell_x_m) >= min_sep]
    if not far:
        return []
    entry = min(far, key=lambda b: zf[b])
    z_exit = zf[exit_idx]
    dist = {entry: 0.0}
    prev = {}
    heap = [(0.0, entry)]
    done = set()
    while heap:
        d, i = heapq.heappop(heap)
        if i in done:
            continue
        done.add(i)
        if i == exit_idx:
            break
        r, c = divmod(i, cols)
        for dr, dc in _D8:
            nr, nc = r + dr, c + dc
            if 0 <= nr < rows and 0 <= nc < cols:
                j = nr * cols + nc
                if j in done:
                    continue
                cost = math.hypot(dr * cell_y_m, dc * cell_x_m) * (1.0 + max(0.0, zf[j] - z_exit) / STEM_RELIEF_M)
                nd = d + cost
                if nd < dist.get(j, math.inf):
                    dist[j] = nd
                    prev[j] = i
                    heapq.heappush(heap, (nd, j))
    if exit_idx not in prev:
        return []
    path = [exit_idx]
    while path[-1] != entry:
        path.append(prev[path[-1]])
    return path[::-1]


def _smooth_profile(surface, ch_order, rcv, donor, channel) -> np.ndarray:
    """Smooth the water surface along each channel and keep it from rising downstream.

    DEM noise (SRTM in narrow canyons: several meters) would otherwise give the river
    surface steps from cell to cell, visible as facets on the water.
    """
    s = surface.copy()
    ch = [i for i in ch_order]
    up = np.array([donor[i] if donor[i] >= 0 else i for i in ch])
    down = np.array([rcv[i] if rcv[i] >= 0 and channel[rcv[i]] else i for i in ch])
    idx = np.array(ch)
    for _ in range(SURFACE_SMOOTH_PASSES):
        s[idx] = 0.25 * s[up] + 0.5 * s[idx] + 0.25 * s[down]
    for i in ch:                      # Upstream to downstream
        r = rcv[i]
        if r >= 0 and channel[r] and s[r] > s[i]:
            s[r] = s[i]
    return s


def channel_hydraulics(discharge, slope, width, manning_n: float):
    """Depth (m), velocity (m/s) and Froude number of a wide channel from Manning's equation."""
    q = np.maximum(np.asarray(discharge, dtype=np.float64), 1e-6)
    s = np.clip(np.asarray(slope, dtype=np.float64), MIN_SLOPE, MAX_HYDRAULIC_SLOPE)
    w = np.maximum(np.asarray(width, dtype=np.float64), MIN_WIDTH_M)
    depth = (q * manning_n / (w * np.sqrt(s))) ** 0.6
    velocity = q / (w * depth)
    froude = velocity / np.sqrt(GRAVITY * depth)
    return depth, velocity, froude


def _smoothstep(x, lo, hi):
    t = np.clip((x - lo) / (hi - lo), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def analyze_river(grid: ElevationGrid, params: RiverParams) -> RiverField:
    rows, cols = grid.shape
    n_cells = rows * cols
    cx = grid.width_m / max(1, cols - 1)
    cy = grid.height_m / max(1, rows - 1)
    z = priority_flood(grid.elevation)
    zf = z.ravel()
    receiver, step = d8_receivers(z, cx, cy)
    rcv = receiver.tolist()

    # Through flow: the main river enters at one edge and leaves at the lowest one. Over flat
    # reservoirs D8 fans the flow into parallel threads, so trace the river as the lowest path
    stem = []
    if params.through_flow:
        # Water leaves the terrain at its lowest edge point
        edge = np.zeros((rows, cols), dtype=bool)
        edge[[0, -1], :] = True
        edge[:, [0, -1]] = True
        exit_idx = int(np.flatnonzero(edge)[np.argmin(zf[edge.ravel()])])
        stem = main_stem_path(z, cx, cy, exit_idx)
        for a, b in zip(stem[:-1], stem[1:]):
            rcv[a] = b
            step[a] = math.hypot((a // cols - b // cols) * cy, (a % cols - b % cols) * cx)
    acc = _accumulate(rcv)
    order = _topological_order(rcv)
    area = np.asarray(acc) * cx * cy
    channel = area >= params.min_area_km2 * 1e6
    # Flow running along the cut edge of the terrain is an artifact of the crop, not a stream
    channel.reshape(rows, cols)[[0, -1], :] = False
    channel.reshape(rows, cols)[:, [0, -1]] = False
    channel[stem] = True

    field = RiverField(
        channel=channel.reshape(rows, cols),
        discharge=np.zeros((rows, cols)),
        surface_m=np.full((rows, cols), np.nan),
        depth_m=np.zeros((rows, cols)),
        velocity=np.zeros((rows, cols, 2)),
        stream_s=np.zeros((rows, cols)),
        stream_n=np.zeros((rows, cols)),
        weight=np.zeros((rows, cols)),
        whitewater=np.zeros((rows, cols)),
        foam=np.zeros((rows, cols)),
    )
    ch_idx = np.nonzero(channel)[0]
    if len(ch_idx) == 0:
        return field
    ch_order = [i for i in order if channel[i]]          # Upstream to downstream

    # Main donor (largest upstream branch) of every channel cell; the stem continues itself
    donor = [-1] * n_cells
    for i in ch_order:
        r = rcv[i]
        if r >= 0 and (donor[r] < 0 or acc[i] > acc[donor[r]]):
            donor[r] = i
    for a, b in zip(stem[:-1], stem[1:]):
        donor[b] = a

    a_max = float(area[ch_idx].max())
    discharge = np.zeros(n_cells)
    discharge[ch_idx] = params.discharge_m3s * area[ch_idx] / a_max
    discharge[stem] = params.discharge_m3s

    # Water-surface slope over a short reach downstream (upstream at outlets)
    slope = np.zeros(n_cells)
    for i in ch_idx.tolist():
        j, dist = i, 0.0
        for _ in range(SLOPE_REACH_CELLS):
            if rcv[j] < 0:
                break
            dist += step[j]
            j = rcv[j]
        if dist == 0.0:
            k = donor[i] if donor[i] >= 0 else i
            dist = step[k] if k != i else 1.0
            slope[i] = (zf[k] - zf[i]) / dist
        else:
            slope[i] = (zf[i] - zf[j]) / dist

    width = np.maximum(WIDTH_COEF * discharge ** WIDTH_EXP, MIN_WIDTH_M)
    depth, speed, froude = channel_hydraulics(discharge, slope, width, params.manning_n)
    white = np.maximum(_smoothstep(froude, 0.8, 1.5), _smoothstep(slope, 0.08, 0.2))

    # Downstream distance (stream coordinate) and foam carried downstream
    to_outlet = np.zeros(n_cells)
    for i in reversed(ch_order):
        r = rcv[i]
        if r >= 0:
            to_outlet[i] = to_outlet[r] + step[i]
    # At confluences foam mixes by discharge: a foaming gully barely whitens a large river
    foam = np.zeros(n_cells)
    in_flow = np.zeros(n_cells)
    in_foam = np.zeros(n_cells)
    for i in ch_order:
        carried = in_foam[i] / in_flow[i] if in_flow[i] > 0.0 else 0.0
        foam[i] = max(white[i], carried)
        r = rcv[i]
        if r >= 0:
            in_flow[r] += discharge[i]
            in_foam[r] += discharge[i] * foam[i] * math.exp(-step[i] / FOAM_DECAY_M)

    # Flow direction: centered difference two cells up and down the main path
    px = (np.arange(n_cells) % cols) * cx
    py = -(np.arange(n_cells) // cols) * cy
    direction = np.zeros((n_cells, 2))
    for i in ch_idx.tolist():
        up, down = i, i
        for _ in range(2):
            if donor[up] >= 0:
                up = donor[up]
            if rcv[down] >= 0:
                down = rcv[down]
        d = np.array([px[down] - px[up], py[down] - py[up]])
        norm = math.hypot(d[0], d[1])
        direction[i] = d / norm if norm > 0 else (0.0, -1.0)

    # Water surface on the channel: the filled cell plus the stage that wets the river width
    # across the local bank slope (a cell-sized DEM sample is wider than a mountain stream)
    half = width / 2.0
    stage = np.zeros(n_cells)
    for i in ch_idx.tolist():
        r, c = divmod(i, cols)
        dx, dy = direction[i]
        banks = []
        for sgn in (1.0, -1.0):
            nc, nr = c + int(round(-dy * sgn)), r - int(round(dx * sgn))
            if 0 <= nr < rows and 0 <= nc < cols:
                banks.append(zf[nr * cols + nc])
        bank_dist = math.hypot(round(dy) * cx, round(dx) * cy) or cx
        bank_slope = max(0.0, (sum(banks) / len(banks) - zf[i]) / bank_dist) if banks else 0.0
        stage[i] = min(max(bank_slope * half[i], MIN_SURFACE_OFFSET_M), max(depth[i], MIN_SURFACE_OFFSET_M))
    surface = _smooth_profile(zf + stage, ch_order, rcv, donor, channel)

    # Spread every channel cell over its width (nearest channel cell wins)
    owner = np.where(channel, np.arange(n_cells), -1).reshape(rows, cols)
    best = np.where(channel, 0.0, np.inf).reshape(rows, cols)
    reach = (half + 2.0 * max(cx, cy)).astype(np.float64)
    gx = px.reshape(rows, cols)
    gy = py.reshape(rows, cols)
    steps = int(math.ceil(float(reach[ch_idx].max()) / min(cx, cy))) + 1
    for _ in range(steps):
        changed = False
        for dr, dc in _D8:
            src = np.full((rows, cols), -1, dtype=np.int64)
            rs = slice(max(0, -dr), rows - max(0, dr))
            rd = slice(max(0, dr), rows - max(0, -dr))
            cs = slice(max(0, -dc), cols - max(0, dc))
            cd = slice(max(0, dc), cols - max(0, -dc))
            src[rd, cd] = owner[rs, cs]
            ok = src >= 0
            o = np.where(ok, src, 0)
            dist = np.hypot(gx - px[o], gy - py[o])
            take = ok & (dist < best) & (dist <= reach[o])
            if take.any():
                owner[take] = src[take]
                best[take] = dist[take]
                changed = True
        if not changed:
            break

    owned = owner >= 0
    o = owner[owned]
    lat = best[owned]
    hw = half[o]
    profile = np.clip(1.0 - (lat / hw) ** 2, 0.0, 1.0)
    rel_x, rel_y = gx[owned] - px[o], gy[owned] - py[o]
    dx, dy = direction[o, 0], direction[o, 1]

    field.discharge.ravel()[ch_idx] = discharge[ch_idx]
    # Water stands level across the channel: the banks, not the surface, set the edge
    field.surface_m[owned] = surface[o]
    field.depth_m[owned] = depth[o] * profile
    field.velocity[owned] = (speed[o] * np.sqrt(profile))[:, None] * direction[o]
    field.stream_s[owned] = -to_outlet[o] + rel_x * dx + rel_y * dy
    field.stream_n[owned] = dx * rel_y - dy * rel_x
    field.weight[owned] = np.clip(1.0 - (lat - hw) / max(cx, cy), 0.0, 1.0)
    edge = np.clip(profile * 2.0, 0.0, 1.0)
    field.whitewater[owned] = white[o] * edge
    field.foam[owned] = foam[o]
    return field
