# SPDX-License-Identifier: GPL-3.0-or-later
"""Standalone tests for the whole-mesh decomposition optimizer — no Blender.

Run from the extension root:
    python3 -m tests.test_decompose
or with pytest:
    pytest reverse_mesh/tests
"""

import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fitting import FitResult, Region  # noqa: E402
from fitting.decompose import (  # noqa: E402
    MeshGraph,
    _accept,
    _is_degenerate,
    grow_regions,
    optimize_decomposition,
)


# --- synthetic mesh builders ---------------------------------------------------

def _grid_surface(point_fn, normal_fn, nu, nv, *, wrap_u=False):
    """Build a MeshGraph for a quad grid over a parametric surface.

    ``point_fn(i, j)`` returns a vertex position on an ``(nu+1)`` (or ``nu`` if
    ``wrap_u``) × ``(nv+1)`` lattice; ``normal_fn(i, j)`` the outward unit normal
    near cell ``(i, j)``. Returns ``(verts, face_vert_idx, centroids, normals,
    areas, adjacency)`` ready to splice into a MeshGraph (possibly merged with
    other patches).
    """
    nu_v = nu if wrap_u else nu + 1
    verts = []
    vid = {}
    for i in range(nu_v):
        for j in range(nv + 1):
            vid[(i, j)] = len(verts)
            verts.append(point_fn(i, j))
    verts = np.array(verts, dtype=float)

    fvi, centroids, normals, areas = [], [], [], []
    cell_id = {}
    for i in range(nu):
        for j in range(nv):
            i1 = (i + 1) % nu if wrap_u else i + 1
            quad = [vid[(i, j)], vid[(i1, j)], vid[(i1, j + 1)], vid[(i, j + 1)]]
            cell_id[(i, j)] = len(fvi)
            p = verts[quad]
            fvi.append(np.array(quad))
            centroids.append(p.mean(axis=0))
            n = np.asarray(normal_fn(i, j), dtype=float)
            normals.append(n / max(np.linalg.norm(n), 1e-12))
            # quad area ≈ |diag1 × diag2| / 2
            areas.append(0.5 * np.linalg.norm(np.cross(p[2] - p[0], p[3] - p[1])))

    adjacency = [[] for _ in fvi]
    for i in range(nu):
        for j in range(nv):
            c = cell_id[(i, j)]
            for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ii = (i + di) % nu if wrap_u else i + di
                jj = j + dj
                if (ii, jj) in cell_id:
                    adjacency[c].append(cell_id[(ii, jj)])
    return (verts, fvi, np.array(centroids), np.array(normals),
            np.array(areas), adjacency)


def _merge(parts):
    """Concatenate several ``_grid_surface`` outputs into one MeshGraph.

    Adjacency stays *within* each part (parts are separate components), which is
    exactly the cube case: each side is internally smooth but split from the others
    by a sharp edge.
    """
    verts = np.zeros((0, 3))
    fvi, cents, norms, areas, adj = [], [], [], [], []
    voff = foff = 0
    for v, f, c, n, a, ad in parts:
        verts = np.vstack([verts, v]) if len(verts) else v
        for fv in f:
            fvi.append(fv + voff)
        cents.append(c)
        norms.append(n)
        areas.append(a)
        for nbrs in ad:
            adj.append([x + foff for x in nbrs])
        voff += len(v)
        foff += len(f)
    return MeshGraph(verts=verts, face_vert_idx=fvi,
                     centroids=np.vstack(cents), normals=np.vstack(norms),
                     areas=np.concatenate(areas), adjacency=adj)


def _plane_part(origin, e1, e2, n, size=4.0, res=6, noise=0.0, seed=0):
    o, e1, e2, nrm = (np.asarray(x, float) for x in (origin, e1, e2, n))
    rng = np.random.default_rng(seed)

    def pf(i, j):
        p = o + (i / res - 0.5) * size * e1 + (j / res - 0.5) * size * e2
        if noise:
            p = p + nrm * rng.normal(0.0, noise)
        return p
    return _grid_surface(pf, lambda i, j: nrm, res, res)


def _cube(size=4.0, res=5):
    h = size
    faces = [
        ((0, 0, h), (1, 0, 0), (0, 1, 0), (0, 0, 1)),     # +Z
        ((0, 0, -h), (1, 0, 0), (0, 1, 0), (0, 0, -1)),   # -Z
        ((h, 0, 0), (0, 1, 0), (0, 0, 1), (1, 0, 0)),     # +X
        ((-h, 0, 0), (0, 1, 0), (0, 0, 1), (-1, 0, 0)),   # -X
        ((0, h, 0), (1, 0, 0), (0, 0, 1), (0, 1, 0)),     # +Y
        ((0, -h, 0), (1, 0, 0), (0, 0, 1), (0, -1, 0)),   # -Y
    ]
    return _merge([_plane_part(o, e1, e2, n, size=2 * h, res=res)
                   for o, e1, e2, n in faces])


def _cylinder_wall(r=2.0, height=8.0, nu=40, nv=10):
    def pf(i, j):
        t = 2 * math.pi * i / nu
        return (r * math.cos(t), r * math.sin(t), height * (j / nv - 0.5))

    def nf(i, j):
        t = 2 * math.pi * (i + 0.5) / nu
        return (math.cos(t), math.sin(t), 0.0)
    return MeshGraph(*(lambda parts: (
        parts[0], parts[1], parts[2], parts[3], parts[4], parts[5]))(
        _grid_surface(pf, nf, nu, nv, wrap_u=True)))


def _fillet_arc(r=1.5, height=6.0, span_deg=90.0, nu=12, nv=8):
    span = math.radians(span_deg)

    def pf(i, j):
        t = -span / 2 + span * i / nu
        return (r * math.cos(t), r * math.sin(t), height * (j / nv - 0.5))

    def nf(i, j):
        t = -span / 2 + span * (i + 0.5) / nu
        return (math.cos(t), math.sin(t), 0.0)
    return MeshGraph(*_grid_surface(pf, nf, nu, nv))


def _roof(dihedral_deg=4.0, size=4.0, res=6):
    """Two plane patches meeting along the y-axis at a shallow dihedral angle."""
    a = math.radians(dihedral_deg / 2.0)
    nL = (-math.sin(a), 0.0, math.cos(a))
    nR = (math.sin(a), 0.0, math.cos(a))
    left = _plane_part((-size / 2 * math.cos(a), 0, size / 2 * math.sin(a)),
                       (math.cos(a), 0, -math.sin(a)), (0, 1, 0), nL, size=size, res=res)
    right = _plane_part((size / 2 * math.cos(a), 0, size / 2 * math.sin(a)),
                        (math.cos(a), 0, math.sin(a)), (0, 1, 0), nR, size=size, res=res)
    g = _merge([left, right])
    # Stitch the two patches together along their shared ridge so coarse
    # segmentation can flood across them (they are one smooth-ish surface).
    nfL = res * res
    for cL in range(nfL):
        for cR in range(nfL, 2 * nfL):
            if np.linalg.norm(g.centroids[cL] - g.centroids[cR]) < size / res * 1.2:
                if cR not in g.adjacency[cL]:
                    g.adjacency[cL].append(cR)
                    g.adjacency[cR].append(cL)
    return g


# --- tests ---------------------------------------------------------------------

def test_grow_regions_splits_cube():
    g = _cube()
    regions = grow_regions(g, math.radians(40.0))
    assert len(regions) == 6, f"cube should segment into 6 sides, got {len(regions)}"


def test_cube_to_six_planes():
    g = _cube()
    out = optimize_decomposition(g, tolerance=0.02, min_faces=3)
    kinds = sorted(r.kind for r in out.results)
    assert kinds == ["PLANE"] * 6, f"expected 6 planes, got {kinds}"
    assert out.coverage > 0.99, f"coverage {out.coverage:.3f}"


def test_plain_cube_min_faces_one():
    # Regression: a plain cube (one quad per side) must decompose with the default
    # min_faces=1 — earlier the default of 4 dropped every side and returned nothing.
    g = _cube(res=1)
    assert g.n_faces == 6
    out = optimize_decomposition(g, tolerance=0.02, min_faces=1)
    kinds = sorted(r.kind for r in out.results)
    assert kinds == ["PLANE"] * 6, f"plain cube should give 6 planes, got {kinds}"


def test_degenerate_oversized_sphere_rejected():
    # A near-flat patch fits a sphere of enormous radius with low RMS; that
    # degenerate fit must be rejected (it caused "oversized spheres at the mesh
    # faces" on capsules), while a real sphere of sane radius is kept.
    scale = 2.0
    giant = FitResult(kind="SPHERE", rms=0.0, max_error=0.0,
                      params={"center": (0, 0, 0), "radius": 500.0, "_scale": scale})
    real = FitResult(kind="SPHERE", rms=0.0, max_error=0.0,
                     params={"center": (0, 0, 0), "radius": 1.0, "_scale": scale})
    assert _is_degenerate(giant) is True
    assert _is_degenerate(real) is False
    # _accept rejects the giant on the degeneracy test alone — it short-circuits
    # before the alignment check, so the region's contents don't matter here.
    pts = np.array([[0, 0, 0.], [1, 0, 0.], [0, 1, 0.]])
    region = Region.from_points(pts, np.tile([0, 0, 1.], (3, 1)))
    assert _accept(giant, region, 0.02, 0.9) is False


def test_cylinder_wall_single_primitive():
    g = _cylinder_wall()
    out = optimize_decomposition(g, tolerance=0.02, min_faces=3)
    kinds = [r.kind for r in out.results]
    assert kinds == ["CYLINDER"], f"smooth wall should be one cylinder, got {kinds}"
    assert out.coverage > 0.99
    # The pool was over-complete (many sub-regions) yet collapsed to one primitive.
    assert out.n_candidates > out.n_primitives


def test_noisy_region_rejected():
    clean = _plane_part((0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1), res=6)
    noisy = _plane_part((20, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1),
                        res=6, noise=1.2, seed=7)
    g = _merge([clean, noisy])
    out = optimize_decomposition(g, tolerance=0.02, min_faces=3)
    assert out.coverage < 0.99, "noisy patch should be left unexplained"
    assert out.leftover_faces, "expected leftover faces from the noisy patch"
    assert len(out.results) == 1 and out.results[0].kind == "PLANE"


def test_normal_disagreement_rejected():
    # A flat patch whose face normals point *in-plane* — fits a plane on points
    # but fails the alignment gate, so nothing is accepted.
    o, e1, e2 = np.array([0, 0, 0.]), np.array([1, 0, 0.]), np.array([0, 1, 0.])

    def pf(i, j):
        return o + (i / 6 - 0.5) * 4 * e1 + (j / 6 - 0.5) * 4 * e2
    g = MeshGraph(*_grid_surface(pf, lambda i, j: (1.0, 0.0, 0.0), 6, 6))  # normal ∥ e1
    out = optimize_decomposition(g, tolerance=0.02, alignment_gate=0.9, min_faces=3)
    assert out.n_primitives == 0, f"misaligned normals should yield nothing, got {out.results}"


def test_fillet_arc_recovered():
    g = _fillet_arc(span_deg=90.0)
    out = optimize_decomposition(g, tolerance=0.02, min_faces=2)
    kinds = [r.kind for r in out.results]
    assert kinds == ["FILLET"], f"a 90° arc should be a trimmed fillet, got {kinds}"


def test_lambda_controls_primitive_count():
    g = _roof(dihedral_deg=4.0)
    # Sweep one angle coarser than the ridge (floods both → a single-plane
    # candidate) and one finer (splits at the ridge → two-plane candidates), so
    # the optimizer genuinely *chooses* how many primitives to keep.
    angles = (40.0, 3.0)
    high = optimize_decomposition(g, angles=angles, tolerance=0.05, min_faces=3,
                                  lam=0.2, merge=False)
    low = optimize_decomposition(g, angles=angles, tolerance=0.05, min_faces=3,
                                 lam=1e-4, merge=False)
    assert high.n_primitives <= low.n_primitives, (
        f"raising λ should not increase primitive count: "
        f"high={high.n_primitives} low={low.n_primitives}")
    assert low.n_primitives >= 2, "low λ should split the roof into two planes"
    assert high.n_primitives == 1, "high λ should keep the roof as one plane"


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ok: {fn.__name__}")
    print(f"All {len(fns)} decompose tests passed.")


if __name__ == "__main__":
    _run_all()
