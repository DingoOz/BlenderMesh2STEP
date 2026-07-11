# SPDX-License-Identifier: GPL-3.0-or-later
"""Standalone tests for the volumetric (boolean-union) solid fitter — no Blender.

Run from the extension root:
    python3 -m tests.test_solidfit
"""

import os
import sys
from collections import Counter

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fitting.solidfit import SDFGrid, fit_solids  # noqa: E402


def _grid(sdf_fn, lo, hi, spacing=0.1):
    """Sample a signed-distance function (positive inside) onto a grid."""
    lo = np.asarray(lo, float)
    hi = np.asarray(hi, float)
    ns = np.ceil((hi - lo) / spacing).astype(int) + 1
    xs = [lo[d] + np.arange(ns[d]) * spacing for d in range(3)]
    gx, gy, gz = np.meshgrid(xs[0], xs[1], xs[2], indexing="ij")
    pts = np.stack([gx, gy, gz], axis=-1)
    sd = sdf_fn(pts)
    return SDFGrid(sd=sd, origin=lo, spacing=spacing)


def _capsule_sdf(r, L):
    """Capsule along z: segment from -L/2..L/2, radius r. Positive inside."""
    def fn(p):
        z = np.clip(p[..., 2], -L / 2, L / 2)
        near = np.stack([np.zeros_like(z), np.zeros_like(z), z], axis=-1)
        return r - np.linalg.norm(p - near, axis=-1)
    return fn


def _sphere_sdf(c, r):
    c = np.asarray(c, float)
    return lambda p: r - np.linalg.norm(p - c, axis=-1)


def _box_sdf(h):
    h = np.asarray(h, float)
    def fn(p):
        d = np.abs(p) - h
        ext = np.linalg.norm(np.maximum(d, 0.0), axis=-1)
        int = np.minimum(np.max(d, axis=-1), 0.0)
        return -(ext + int)                      # positive inside
    return fn


def test_capsule_is_cylinder_plus_two_spheres():
    g = _grid(_capsule_sdf(1.0, 3.0), [-1.3, -1.3, -2.8], [1.3, 1.3, 2.8], 0.1)
    results, cov = fit_solids(g)
    kinds = Counter(r.kind for r in results)
    print("capsule:", dict(kinds), f"coverage={cov:.2f}",
          [r.summary for r in results])
    assert kinds["CYLINDER"] >= 1, f"expected a cylinder, got {dict(kinds)}"
    assert kinds["SPHERE"] >= 2, f"expected 2 end-cap spheres, got {dict(kinds)}"
    cyl = next(r for r in results if r.kind == "CYLINDER")
    assert abs(cyl.params["radius"] - 1.0) < 0.15, cyl.params["radius"]
    assert abs(abs(cyl.params["axis"][2]) - 1.0) < 0.05, "cylinder axis should be ~Z"
    assert cyl.params["height"] > 2.0, cyl.params["height"]
    assert cov > 0.85, f"coverage too low: {cov:.2f}"


def test_plain_sphere_is_one_sphere():
    g = _grid(_sphere_sdf((0, 0, 0), 1.5), [-1.7, -1.7, -1.7], [1.7, 1.7, 1.7], 0.1)
    results, cov = fit_solids(g)
    assert results[0].kind == "SPHERE"
    assert abs(results[0].params["radius"] - 1.5) < 0.15
    assert cov > 0.9, cov


def test_box_is_one_box():
    g = _grid(_box_sdf([1.0, 1.4, 0.8]), [-1.6, -2.0, -1.4], [1.6, 2.0, 1.4], 0.1)
    results, cov = fit_solids(g)
    print("box:", [r.summary for r in results], f"coverage={cov:.2f}")
    assert any(r.kind == "BOX" for r in results), [r.kind for r in results]
    assert cov > 0.85, cov


def _torus_sdf(R, r):
    """Ring torus about z. Positive inside."""
    def fn(p):
        rho = np.hypot(p[..., 0], p[..., 1])
        return r - np.hypot(rho - R, p[..., 2])
    return fn


def _cone_sdf(r0, r1, h):
    """Frustum along z from 0..h, radius r0 at the base, r1 at the top.

    Approximate interior signed distance: min of the (perpendicular) lateral
    clearance and the two cap clearances — exact enough for inradius profiles.
    """
    import math
    slope = (r1 - r0) / h
    cosa = math.cos(math.atan(abs(slope)))
    def fn(p):
        z = p[..., 2]
        rho = np.hypot(p[..., 0], p[..., 1])
        r_at = r0 + slope * np.clip(z, 0.0, h)
        lat = (r_at - rho) * cosa
        return np.minimum(np.minimum(lat, z), h - z)
    return fn


def test_torus_is_one_torus():
    g = _grid(_torus_sdf(2.0, 0.6), [-2.8, -2.8, -0.8], [2.8, 2.8, 0.8], 0.1)
    results, cov = fit_solids(g)
    print("torus:", [r.summary for r in results], f"coverage={cov:.2f}")
    assert results and results[0].kind == "TORUS", [r.kind for r in results]
    p = results[0].params
    assert abs(p["major_radius"] - 2.0) < 0.15, p
    assert abs(p["minor_radius"] - 0.6) < 0.12, p
    assert cov > 0.8, cov


def test_cone_is_one_cone():
    g = _grid(_cone_sdf(1.6, 0.4, 2.4), [-1.8, -1.8, -0.2], [1.8, 1.8, 2.6], 0.08)
    results, cov = fit_solids(g)
    print("cone:", [r.summary for r in results], f"coverage={cov:.2f}")
    assert results and results[0].kind == "CONE", [r.kind for r in results]
    assert cov > 0.75, cov


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ok: {fn.__name__}")
    print(f"All {len(fns)} solidfit tests passed.")


if __name__ == "__main__":
    _run_all()
