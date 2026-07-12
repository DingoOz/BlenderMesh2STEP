# SPDX-License-Identifier: GPL-3.0-or-later
"""Geometric statistics for a mesh part — pure NumPy, no Blender dependency.

Given world-space vertices and a triangulation, compute the CAD stats a machine
shop cares about: bounding-box size, enclosed volume, surface area, and the
centre of mass (centroid of the enclosed solid). Watertightness is a topological
property of the *polygon* mesh (an edge shared by exactly two faces) and is
determined by the caller from the mesh's edge/face adjacency, then passed in for
formatting — it cannot be recovered from a bare triangle list.

Volume and centre of mass are only physically meaningful for a closed mesh;
the caller flags the result when the mesh is not watertight.
"""

from __future__ import annotations

import numpy as np


def bounding_box(verts):
    """Return ``(lo, hi, size)`` — min corner, max corner, and the extents."""
    v = np.asarray(verts, dtype=float)
    lo = v.min(axis=0)
    hi = v.max(axis=0)
    return lo, hi, hi - lo


def surface_area(verts, tris):
    """Total surface area of the triangulation."""
    v = np.asarray(verts, dtype=float)
    t = np.asarray(tris, dtype=int)
    if len(t) == 0:
        return 0.0
    a, b, c = v[t[:, 0]], v[t[:, 1]], v[t[:, 2]]
    return float(np.sum(np.linalg.norm(np.cross(b - a, c - a), axis=1)) * 0.5)


def signed_volume(verts, tris):
    """Signed volume via the divergence theorem over origin tetrahedra.

    Sum of ``a · (b × c) / 6`` over triangles. Positive when the triangulation
    is consistently outward-facing; the magnitude is the enclosed volume for a
    closed mesh. Returns the *signed* value so the caller can note flipped
    normals (negative) as a data-quality signal.
    """
    v = np.asarray(verts, dtype=float)
    t = np.asarray(tris, dtype=int)
    if len(t) == 0:
        return 0.0
    a, b, c = v[t[:, 0]], v[t[:, 1]], v[t[:, 2]]
    return float(np.sum(np.einsum("ij,ij->i", a, np.cross(b, c))) / 6.0)


def centre_of_mass(verts, tris):
    """Centroid of the enclosed solid (origin-tetrahedron weighted mean).

    Returns ``None`` if the net volume is degenerate. Meaningful only for a
    closed, consistently-oriented mesh.
    """
    v = np.asarray(verts, dtype=float)
    t = np.asarray(tris, dtype=int)
    if len(t) == 0:
        return None
    a, b, c = v[t[:, 0]], v[t[:, 1]], v[t[:, 2]]
    vol6 = np.einsum("ij,ij->i", a, np.cross(b, c))     # 6× tetra volume
    total = float(np.sum(vol6))
    if abs(total) < 1e-18:
        return None
    tetra_centroid = (a + b + c) * 0.25                 # 4th vertex is origin (0)
    com = np.einsum("i,ij->j", vol6, tetra_centroid) / total
    return com


def part_stats(verts, tris):
    """Bundle the geometric stats into one dict (all in the input's units)."""
    lo, hi, size = bounding_box(verts)
    return {
        "bbox_lo": lo,
        "bbox_hi": hi,
        "size": size,
        "area": surface_area(verts, tris),
        "signed_volume": signed_volume(verts, tris),
        "com": centre_of_mass(verts, tris),
        "n_tris": int(len(np.asarray(tris, dtype=int))),
    }
