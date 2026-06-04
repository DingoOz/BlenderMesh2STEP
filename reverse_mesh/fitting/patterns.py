# SPDX-License-Identifier: GPL-3.0-or-later
"""Pattern / symmetry discovery for feature propagation.

Given one fitted hole (a seed cylinder) and the cylinders recovered from every
other region of the mesh, find the instances that match the seed under any rigid
motion — same radius, parallel axis — and classify how they're arranged
(circular bolt pattern, linear array, or scattered). This is the symmetry-
agnostic way to "fit one hole, get the rest": instead of guessing a mirror plane
or rotation, match by the shape parameters that a real instance must share.

Pure NumPy, no Blender — the operator extracts ``{radius, axis, center}`` dicts
from its cluster fits and calls in here.
"""

from __future__ import annotations

import numpy as np


def _unit(v):
    v = np.asarray(v, dtype=float)
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-12 else v


def match_cylinders(seed, candidates, radius_tol=0.05, axis_tol_deg=5.0,
                    height_ratio=0.5):
    """Indices of ``candidates`` matching the ``seed`` cylinder.

    ``seed`` and each candidate are dicts ``{"radius", "axis", "center"}`` (with
    an optional ``"height"``); ``None`` candidates are skipped. A match has a
    radius within ``radius_tol × seed_radius`` and an axis parallel within
    ``axis_tol_deg`` (direction-agnostic — a hole drilled from either side
    counts). When both carry a height, a candidate shorter than
    ``height_ratio × seed_height`` is rejected, so a flat cap disk — which fits as
    a zero-height cylinder of the same radius — is not mistaken for a hole wall.
    """
    sr = float(seed["radius"])
    sa = _unit(seed["axis"])
    sh = seed.get("height")
    cos_tol = np.cos(np.radians(axis_tol_deg))
    out = []
    for i, c in enumerate(candidates):
        if c is None:
            continue
        if abs(float(c["radius"]) - sr) > radius_tol * max(sr, 1e-9):
            continue
        if abs(float(np.dot(_unit(c["axis"]), sa))) < cos_tol:
            continue
        if sh is not None and c.get("height") is not None \
                and float(c["height"]) < height_ratio * float(sh):
            continue
        out.append(i)
    return out


def classify_arrangement(centers, axis):
    """Describe how instance ``centers`` are arranged about ``axis``.

    Returns ``(kind, info)`` where kind is ``"SINGLE"``, ``"CIRCULAR"`` (a bolt
    circle — info has ``count``/``radius``/``center``), ``"LINEAR"`` (an array),
    or ``"SCATTERED"``.
    """
    centers = np.asarray(centers, dtype=float)
    n = len(centers)
    if n < 2:
        return "SINGLE", {"count": n}

    centroid = centers.mean(axis=0)
    rel = centers - centroid

    # Circular: equal distance from the common centre (within the pattern plane).
    ax = _unit(axis)
    proj = rel - np.outer(rel @ ax, ax)
    dists = np.linalg.norm(proj, axis=1)
    if n >= 3 and dists.mean() > 1e-9 and dists.std() < 0.05 * dists.mean():
        return "CIRCULAR", {"count": int(n), "radius": float(dists.mean()),
                            "center": centroid.tolist()}

    # Linear: collinear (second singular value of the centred points is tiny).
    _, s, _ = np.linalg.svd(rel, full_matrices=False)
    if len(s) >= 2 and s[0] > 1e-9 and s[1] < 0.05 * s[0]:
        return "LINEAR", {"count": int(n)}

    return "SCATTERED", {"count": int(n)}
