# SPDX-License-Identifier: GPL-3.0-or-later
"""2D profile toolkit for extruded (prismatic) solids.

A *profile* is a closed 2D loop of segments in the extrusion frame
(u along ``xdir``, v along ``ydir = axis × xdir``), encoded as an ``(S, 8)``
float array — one row per segment::

    [type, sx, sy, ex, ey, cx, cy, ccw]

``type`` is 0 for a LINE (center/ccw unused, zero) and 1 for a circular ARC
from (sx, sy) to (ex, ey) about center (cx, cy), counter-clockwise when
``ccw`` is 1. Segments are ordered so each row's end is the next row's start,
and the loop is oriented counter-clockwise (positive enclosed area).

Everything here is pure NumPy so the fitters and both STEP writers can share
it, and it is unit-testable without Blender.
"""

from __future__ import annotations

import math

import numpy as np

LINE = 0.0
ARC = 1.0

# A vertex whose boundary turn exceeds this is a corner (a real profile vertex);
# gentler constant turns are arc tessellation. Regular octagons (45°) stay
# polygons; circles tessellated at 16+ segments (≤22.5°) become arcs.
CORNER_ANGLE = math.radians(35.0)

# Minimum number of consecutive edges required to accept an arc run.
MIN_ARC_EDGES = 3


def _quantize(pt, tol):
    return (round(pt[0] / tol), round(pt[1] / tol))


def chain_segments(segments, tol):
    """Chain unordered 2-point segments into ordered closed loops.

    ``segments`` is a sequence of ((u0, v0), (u1, v1)) pairs; endpoints within
    ``tol`` are considered coincident. Duplicate segments (the same endpoint
    pair) collapse to one. Returns a list of loops, each an (M, 2) array of
    ordered vertices (closed implicitly: last connects back to first), or an
    empty list if any chain fails to close or a junction is non-manifold.
    """
    # Deduplicate by quantised endpoint pair.
    seen = set()
    uniq = []
    for a, b in segments:
        key = frozenset((_quantize(a, tol), _quantize(b, tol)))
        if len(key) < 2 or key in seen:
            continue
        seen.add(key)
        uniq.append((tuple(a), tuple(b)))
    if not uniq:
        return []

    # Adjacency: each endpoint must join exactly two segments (a manifold loop).
    adj = {}
    for i, (a, b) in enumerate(uniq):
        for p in (_quantize(a, tol), _quantize(b, tol)):
            adj.setdefault(p, []).append(i)
    if any(len(v) != 2 for v in adj.values()):
        return []

    unused = set(range(len(uniq)))
    loops = []
    while unused:
        i = unused.pop()
        a, b = uniq[i]
        loop = [np.asarray(a, dtype=float)]
        head = b
        head_key = _quantize(b, tol)
        start_key = _quantize(a, tol)
        while head_key != start_key:
            loop.append(np.asarray(head, dtype=float))
            nxt = [j for j in adj[head_key] if j in unused]
            if not nxt:
                return []                      # open chain — not a closed profile
            j = nxt[0]
            unused.discard(j)
            a2, b2 = uniq[j]
            head = b2 if _quantize(a2, tol) == head_key else a2
            head_key = _quantize(head, tol)
        loops.append(np.asarray(loop))
    return [lp for lp in loops if len(lp) >= 3]


def polygon_area(pts):
    """Signed area of a closed polygon (positive = counter-clockwise)."""
    x = pts[:, 0]
    y = pts[:, 1]
    return 0.5 * float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))


def _turn_angles(pts):
    """Signed turn angle at each vertex of a closed polyline."""
    d = np.roll(pts, -1, axis=0) - pts                 # edge i: pts[i] → pts[i+1]
    prev = np.roll(d, 1, axis=0)                       # edge into vertex i
    cross = prev[:, 0] * d[:, 1] - prev[:, 1] * d[:, 0]
    dot = np.sum(prev * d, axis=1)
    return np.arctan2(cross, dot)


def _fit_arc_run(pts):
    """Circle through a run of points; returns (center, radius, max_residual)."""
    from .common import fit_circle_2d

    center, radius, radial = fit_circle_2d(np.asarray(pts))
    return center, radius, float(np.max(np.abs(radial))) if len(radial) else 0.0


def segment_loop(pts, scale, corner_angle=CORNER_ANGLE):
    """Segment a closed CCW vertex loop into LINE/ARC profile rows.

    Arc detection is geometric (seed-and-grow): a seed of three consecutive
    edges with gentle, same-sign, near-equal turns fixes a candidate circle,
    which then grows in both directions as long as the next vertex still lies
    on it. A tangent line→arc junction vertex lies ON the circle, so growth
    captures the arc's exact endpoints, while the far end of an adjoining
    straight edge falls off the circle and stops the growth — turn angles
    alone cannot make that distinction. Runs of ≥ :data:`MIN_ARC_EDGES` edges
    become one ARC row; every remaining edge is a LINE, merged across
    collinear vertices. A loop whose single grown circle covers every edge is
    a full circle — a cylinder's profile, not an extrusion's — and yields
    ``None``.
    """
    pts = np.asarray(pts, dtype=float)
    m = len(pts)
    if m < 3:
        return None
    if polygon_area(pts) < 0:
        pts = pts[::-1].copy()

    turns = _turn_angles(pts)
    straight = math.radians(0.5)
    fit_tol = max(1e-5 * scale, 1e-12)

    def on_circle(idx, center, radius):
        return abs(float(np.linalg.norm(pts[idx % m] - center)) - radius) <= fit_tol

    covered = np.zeros(m, dtype=bool)          # per-edge: edge k = pts[k]→pts[k+1]
    arc_rows = []                              # (first_edge_index, row)
    for k0 in range(m):
        if any(covered[(k0 + i) % m] for i in range(3)):
            continue
        t1, t2 = turns[(k0 + 1) % m], turns[(k0 + 2) % m]
        gentle = (straight <= abs(t1) < corner_angle
                  and straight <= abs(t2) < corner_angle)
        if not gentle or t1 * t2 <= 0 or abs(abs(t1) - abs(t2)) > 0.25 * abs(t1):
            continue
        seed = [(k0 + i) % m for i in range(4)]
        center, radius, res = _fit_arc_run(pts[seed])
        if res > fit_tol:
            continue
        lo, hi = k0, (k0 + 3) % m              # vertex range [lo..hi] on the circle
        n_edges = 3
        while n_edges < m and not covered[hi % m] and on_circle(hi + 1, center, radius):
            hi = (hi + 1) % m
            n_edges += 1
        while n_edges < m and not covered[(lo - 1) % m] and on_circle(lo - 1, center, radius):
            lo = (lo - 1) % m
            n_edges += 1
        if n_edges >= m:
            # The circle swallowed the whole loop: a full-circle profile.
            if not arc_rows and not np.any(covered):
                return None
            continue
        run = [(lo + i) % m for i in range(n_edges + 1)]
        center, radius, res = _fit_arc_run(pts[run])   # refit on the full run
        if res > fit_tol or n_edges < MIN_ARC_EDGES:
            continue
        ccw = bool(turns[run[1]] > 0)
        arc_rows.append((lo, _arc_row(pts[lo], pts[hi % m], center, ccw)))
        for k in run[:-1]:
            covered[k % m] = True

    # Remaining edges are lines; merge across straight (collinear) vertices.
    line_rows = []
    edge_used = np.zeros(m, dtype=bool)
    for k0 in range(m):
        if covered[k0] or edge_used[k0]:
            continue
        a = k0
        while True:
            p = (a - 1) % m
            if p == k0 or covered[p] or edge_used[p] or abs(turns[a]) >= straight:
                break
            a = p
        chain = [a]
        edge_used[a] = True
        k = (a + 1) % m
        while (k != a and not covered[k] and not edge_used[k]
               and abs(turns[k]) < straight):
            edge_used[k] = True
            chain.append(k)
            k = (k + 1) % m
        line_rows.append((chain[0],
                          _line_row(pts[chain[0]], pts[(chain[-1] + 1) % m])))

    rows = [row for _k, row in sorted(arc_rows + line_rows, key=lambda t: t[0])]
    rows = _merge_rows(rows, max(1e-9 * scale, 1e-12))
    return np.asarray(rows) if rows else None


def _merge_rows(rows, tol):
    """Collapse consecutive rows that continue the same line or the same arc.

    Detection can fragment one straight side into collinear lines (or one arc
    into same-circle pieces); geometry is unchanged by re-joining them. Works
    cyclically so fragments across the list seam merge too.
    """
    def try_merge(a, b):
        if a[0] != b[0] or (abs(a[3] - b[1]) > tol or abs(a[4] - b[2]) > tol):
            return None
        if a[0] == LINE:
            dx1, dy1 = a[3] - a[1], a[4] - a[2]
            dx2, dy2 = b[3] - b[1], b[4] - b[2]
            if abs(dx1 * dy2 - dy1 * dx2) <= tol * math.hypot(dx1, dy1):
                return _line_row((a[1], a[2]), (b[3], b[4]))
            return None
        same_c = abs(a[5] - b[5]) <= tol and abs(a[6] - b[6]) <= tol
        if same_c and a[7] == b[7] and abs(arc_radius(a) - arc_radius(b)) <= tol:
            merged = _arc_row((a[1], a[2]), (b[3], b[4]), (a[5], a[6]), a[7] > 0.5)
            # Never merge into a (near-)full circle: keep two rows instead.
            a0, a1 = arc_angles(merged)
            if abs(a1 - a0) < math.radians(345.0):
                return merged
        return None

    rows = [list(r) for r in rows]
    changed = True
    while changed and len(rows) > 1:
        changed = False
        for i in range(len(rows)):
            j = (i + 1) % len(rows)
            if i == j:
                break
            m = try_merge(rows[i], rows[j])
            if m is not None:
                rows[i] = m
                del rows[j]
                changed = True
                break
    return rows


def _line_row(s, e):
    return [LINE, float(s[0]), float(s[1]), float(e[0]), float(e[1]), 0.0, 0.0, 0.0]


def _arc_row(s, e, c, ccw):
    return [ARC, float(s[0]), float(s[1]), float(e[0]), float(e[1]),
            float(c[0]), float(c[1]), 1.0 if ccw else 0.0]


def arc_angles(row):
    """Start/end angles (end adjusted for direction) of an ARC row."""
    _, sx, sy, ex, ey, cx, cy, ccw = row
    a0 = math.atan2(sy - cy, sx - cx)
    a1 = math.atan2(ey - cy, ex - cx)
    if ccw > 0.5:
        while a1 <= a0 + 1e-12:
            a1 += 2 * math.pi
    else:
        while a1 >= a0 - 1e-12:
            a1 -= 2 * math.pi
    return a0, a1


def arc_radius(row):
    return math.hypot(row[1] - row[5], row[2] - row[6])


def arc_midpoint(row):
    """The point halfway along an ARC row (for 3-point arc construction)."""
    a0, a1 = arc_angles(row)
    am = (a0 + a1) / 2.0
    r = arc_radius(row)
    return (row[5] + r * math.cos(am), row[6] + r * math.sin(am))


def profile_area(profile):
    """Signed enclosed area of a profile (positive for a CCW loop).

    Polygon area over the segment endpoints, plus the circular-segment area
    between each ARC's chord and its arc (positive when the arc bulges out of
    the polygon on a CCW loop).
    """
    profile = np.asarray(profile, dtype=float)
    pts = profile[:, 1:3]
    area = polygon_area(pts)
    for row in profile:
        if row[0] == ARC:
            a0, a1 = arc_angles(row)
            theta = a1 - a0                                  # signed sweep
            r = arc_radius(row)
            area += 0.5 * r * r * (theta - math.sin(theta))
    return float(area)


def profile_perimeter(profile):
    total = 0.0
    for row in np.asarray(profile, dtype=float):
        if row[0] == ARC:
            a0, a1 = arc_angles(row)
            total += abs(a1 - a0) * arc_radius(row)
        else:
            total += math.hypot(row[3] - row[1], row[4] - row[2])
    return float(total)


def distance_to_profile(profile, uv):
    """Unsigned distance from 2D points ``uv`` (N, 2) to the profile boundary."""
    profile = np.asarray(profile, dtype=float)
    uv = np.asarray(uv, dtype=float)
    best = np.full(len(uv), np.inf)
    for row in profile:
        if row[0] == ARC:
            c = row[5:7]
            r = arc_radius(row)
            d = uv - c
            ang = np.arctan2(d[:, 1], d[:, 0])
            a0, a1 = arc_angles(row)
            lo, hi = min(a0, a1), max(a0, a1)
            two_pi = 2 * math.pi
            k = np.floor((ang - lo) / two_pi)
            on_arc = ((ang - k * two_pi >= lo - 1e-9) &
                      (ang - k * two_pi <= hi + 1e-9))
            radial = np.abs(np.linalg.norm(d, axis=1) - r)
            d_ends = np.minimum(np.linalg.norm(uv - row[1:3], axis=1),
                                np.linalg.norm(uv - row[3:5], axis=1))
            best = np.minimum(best, np.where(on_arc, radial, d_ends))
        else:
            s, e = row[1:3], row[3:5]
            seg = e - s
            ll = float(np.dot(seg, seg))
            t = np.clip((uv - s) @ seg / ll, 0.0, 1.0) if ll > 1e-18 else np.zeros(len(uv))
            proj = s + np.outer(t, seg)
            best = np.minimum(best, np.linalg.norm(uv - proj, axis=1))
    return best


def outward_normals(profile, uv):
    """Outward 2D unit normal of the nearest profile segment, per point.

    Assumes a CCW-oriented profile (the loop convention): a LINE's outward
    normal is its direction rotated −90°; an ARC's is radially outward when it
    turns CCW (convex bulge) and radially inward when CW (concave).
    """
    profile = np.asarray(profile, dtype=float)
    uv = np.asarray(uv, dtype=float)
    best = np.full(len(uv), np.inf)
    out = np.zeros_like(uv)
    for row in profile:
        if row[0] == ARC:
            c = row[5:7]
            r = arc_radius(row)
            d = uv - c
            dist = np.abs(np.linalg.norm(d, axis=1) - r)
            radial = d / np.clip(np.linalg.norm(d, axis=1, keepdims=True), 1e-12, None)
            n = radial if row[7] > 0.5 else -radial
        else:
            s, e = row[1:3], row[3:5]
            seg = e - s
            ll = float(np.dot(seg, seg))
            t = np.clip((uv - s) @ seg / ll, 0.0, 1.0) if ll > 1e-18 else np.zeros(len(uv))
            proj = s + np.outer(t, seg)
            dist = np.linalg.norm(uv - proj, axis=1)
            ln = math.sqrt(ll) if ll > 1e-18 else 1.0
            n = np.tile((seg[1] / ln, -seg[0] / ln), (len(uv), 1))
        closer = dist < best
        best = np.where(closer, dist, best)
        out[closer] = n[closer]
    return out


def tessellate(profile, segments=48):
    """Ordered 2D polyline of the profile (one entry per vertex, no repeat of
    the first). Arcs get a vertex count proportional to their sweep."""
    profile = np.asarray(profile, dtype=float)
    pts = []
    for row in profile:
        pts.append((float(row[1]), float(row[2])))
        if row[0] == ARC:
            a0, a1 = arc_angles(row)
            r = arc_radius(row)
            n = max(2, int(round(segments * abs(a1 - a0) / (2 * math.pi))))
            for k in range(1, n):
                a = a0 + (a1 - a0) * k / n
                pts.append((row[5] + r * math.cos(a), row[6] + r * math.sin(a)))
    return np.asarray(pts)


def ngon_profile(sides, radius):
    """Regular N-gon profile (circumradius ``radius``), CCW, one LINE per side."""
    sides = max(3, int(sides))
    rows = []
    for i in range(sides):
        a0 = 2 * math.pi * i / sides
        a1 = 2 * math.pi * (i + 1) / sides
        rows.append(_line_row((radius * math.cos(a0), radius * math.sin(a0)),
                              (radius * math.cos(a1), radius * math.sin(a1))))
    return np.asarray(rows)
