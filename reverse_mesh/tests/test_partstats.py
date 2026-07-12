# SPDX-License-Identifier: GPL-3.0-or-later
"""Standalone tests for the part-stats math — no Blender.

    python3 reverse_mesh/tests/test_partstats.py
"""

import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import partstats  # noqa: E402


def fail(m):
    print("[FAIL]", m)
    sys.exit(1)


def _box_mesh(hx, hy, hz, centre=(0.0, 0.0, 0.0)):
    """Closed, outward-oriented triangulated box (verts, tris)."""
    c = np.asarray(centre, float)
    s = np.array([[sx, sy, sz] for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)])
    verts = c + s * np.array([hx, hy, hz])
    # 12 outward triangles (CCW seen from outside).
    quads = [
        (0, 1, 3, 2),   # -X
        (4, 6, 7, 5),   # +X
        (0, 4, 5, 1),   # -Y
        (2, 3, 7, 6),   # +Y
        (0, 2, 6, 4),   # -Z
        (1, 5, 7, 3),   # +Z
    ]
    tris = []
    for a, b, cc, d in quads:
        tris += [(a, b, cc), (a, cc, d)]
    return verts, np.array(tris)


def main():
    hx, hy, hz = 2.0, 3.0, 4.0
    verts, tris = _box_mesh(hx, hy, hz, centre=(1.0, -2.0, 0.5))

    lo, hi, size = partstats.bounding_box(verts)
    if not np.allclose(size, [2 * hx, 2 * hy, 2 * hz]):
        fail(f"bbox size {size} != {[2*hx, 2*hy, 2*hz]}")

    vol = partstats.signed_volume(verts, tris)
    want_vol = (2 * hx) * (2 * hy) * (2 * hz)
    if abs(abs(vol) - want_vol) > 1e-9:
        fail(f"volume {vol} != ±{want_vol}")
    if vol < 0:
        fail("outward-oriented box should give positive signed volume")

    area = partstats.surface_area(verts, tris)
    want_area = 2 * ((2 * hx) * (2 * hy) + (2 * hx) * (2 * hz) + (2 * hy) * (2 * hz))
    if abs(area - want_area) > 1e-9:
        fail(f"area {area} != {want_area}")

    com = partstats.centre_of_mass(verts, tris)
    if com is None or not np.allclose(com, [1.0, -2.0, 0.5], atol=1e-9):
        fail(f"centre of mass {com} != box centre")

    # Flipped normals → negative signed volume, same magnitude.
    flipped = tris[:, ::-1]
    if partstats.signed_volume(verts, flipped) > 0:
        fail("inward-oriented box should give negative signed volume")

    # A closed-form sanity check on a unit tetrahedron.
    tv = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], float)
    tt = np.array([(0, 2, 1), (0, 1, 3), (0, 3, 2), (1, 2, 3)])
    if abs(abs(partstats.signed_volume(tv, tt)) - 1.0 / 6.0) > 1e-12:
        fail("unit tetra volume != 1/6")

    # part_stats bundles everything.
    st = partstats.part_stats(verts, tris)
    if st["n_tris"] != 12 or abs(abs(st["signed_volume"]) - want_vol) > 1e-9:
        fail(f"part_stats bundle wrong: {st}")

    print("ALL PARTSTATS TESTS PASSED")


if __name__ == "__main__":
    main()
