# SPDX-License-Identifier: GPL-3.0-or-later
"""Volumetric primitive fitting: cover a solid's *volume* with a union of solids.

Where :mod:`reverse_mesh.fitting.decompose` segments the *surface* into patches,
this reasons about the enclosed *volume* and recovers an additive-CSG approximation
— a union of inscribed solid primitives (sphere / cylinder / box). A capsule comes
back as ``cylinder ∪ sphere ∪ sphere`` regardless of tessellation, because the
algorithm looks at what's inside, not at the surface triangles.

Pure NumPy, no Blender. The input is a signed-distance grid (positive inside,
negative outside) which the operator layer fills from a BVH; the output is a list
of :class:`FitResult` solids meant to be **unioned** (all role ADD) — ideally via
the OCCT boolean export, which already fuses ADD solids into one watertight body.

Algorithm (greedy maximal-inscribed cover):
    while uncovered interior remains and budget allows:
        propose the largest sphere / cylinder / box that fits *inside* the volume
        keep the one covering the most still-uncovered interior; mark it covered
Each primitive is contained by construction (radii come from the distance field),
so nothing juts outside the original surface — the artifact the surface path hits.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .common import FitResult, region_scale
from .primitives import summarize


@dataclass
class SDFGrid:
    """A signed-distance sampling of a solid on a regular grid.

    ``sd[i, j, k]`` is the signed distance at world point ``origin + (i, j, k) *
    spacing`` — positive inside the solid, negative outside.
    """

    sd: np.ndarray            # (nx, ny, nz)
    origin: np.ndarray        # (3,) world coord of voxel (0, 0, 0)
    spacing: float

    def coords(self) -> np.ndarray:
        """(nx, ny, nz, 3) world coordinate of every voxel centre."""
        nx, ny, nz = self.sd.shape
        i = np.arange(nx)
        j = np.arange(ny)
        k = np.arange(nz)
        gi, gj, gk = np.meshgrid(i, j, k, indexing="ij")
        return self.origin + np.stack([gi, gj, gk], axis=-1) * self.spacing

    def sample(self, pts: np.ndarray) -> np.ndarray:
        """Nearest-voxel signed distance at arbitrary world points.

        Points outside the grid return a large negative value (treated as solidly
        outside), so a primitive poking past the grid is never deemed contained.
        """
        nx, ny, nz = self.sd.shape
        idx = np.rint((pts - self.origin) / self.spacing).astype(int)
        inside = (
            (idx[:, 0] >= 0) & (idx[:, 0] < nx)
            & (idx[:, 1] >= 0) & (idx[:, 1] < ny)
            & (idx[:, 2] >= 0) & (idx[:, 2] < nz)
        )
        out = np.full(len(pts), -1e18)
        ii = idx[inside]
        out[inside] = self.sd[ii[:, 0], ii[:, 1], ii[:, 2]]
        return out


# --- candidate solids ----------------------------------------------------------

def _sphere_candidate(coords, sd, remaining):
    """Largest inscribed sphere centred on the most-interior uncovered voxel."""
    masked = np.where(remaining, sd, -np.inf)
    flat = int(np.argmax(masked))
    if not np.isfinite(masked.flat[flat]) or masked.flat[flat] <= 0:
        return None
    idx = np.unravel_index(flat, sd.shape)
    centre = coords[idx]
    radius = float(sd[idx])
    params = {"center": centre, "radius": radius}
    return FitResult(kind="SPHERE", rms=0.0, max_error=0.0, params=params)


def _principal_axes(pts):
    """Unit principal axes of a point set, major first (PCA)."""
    c = pts.mean(axis=0)
    _, _, vh = np.linalg.svd(pts - c, full_matrices=False)
    return c, vh


def _cylinder_for_axis(coords, sd, remaining, axis, grid, contain_tol):
    """Best volume inscribed cylinder along ``axis`` over the uncovered interior."""
    pts = coords[remaining]
    sdv = sd[remaining]
    if len(pts) < 8:
        return None
    axis = axis / max(np.linalg.norm(axis), 1e-12)
    c = pts.mean(axis=0)
    t = (pts - c) @ axis
    # Per-slice inradius ≈ the clearance of the most-interior voxel in that slice.
    nb = max(8, int((t.max() - t.min()) / grid.spacing))
    edges = np.linspace(t.min(), t.max(), nb + 1)
    binid = np.clip(np.digitize(t, edges) - 1, 0, nb - 1)
    inradius = np.zeros(nb)
    for b in range(nb):
        m = binid == b
        inradius[b] = sdv[m].max() if np.any(m) else 0.0
    centres = 0.5 * (edges[:-1] + edges[1:])

    best = None
    for r in np.unique(inradius):
        if r <= 0:
            continue
        ok = inradius >= r - 1e-9
        # longest contiguous run of slices that admit radius r
        run0 = run_best = -1
        cur = None
        for b in range(nb):
            if ok[b]:
                cur = b if cur is None else cur
                if run_best < 0 or (b - cur) > (run_best - run0):
                    run0, run_best = cur, b
            else:
                cur = None
        if run0 < 0:
            continue
        t0, t1 = centres[run0], centres[run_best]
        height = float(t1 - t0)
        if height <= grid.spacing:
            continue
        vol = np.pi * r * r * height
        if best is None or vol > best[0]:
            best = (vol, float(r), t0, t1)
    if best is None:
        return None
    _, radius, t0, t1 = best
    base = c + axis * (0.5 * (t0 + t1))
    height = float(t1 - t0)
    cyl = FitResult(kind="CYLINDER", rms=0.0, max_error=0.0,
                    params={"base": base, "axis": axis, "radius": radius, "height": height})
    if not _contained(cyl, grid, contain_tol):
        return None
    return cyl


def _cylinder_candidates(coords, sd, remaining, grid, contain_tol):
    pts = coords[remaining]
    if len(pts) < 8:
        return []
    _, vh = _principal_axes(pts)
    axes = [vh[0], np.array([1.0, 0, 0]), np.array([0, 1.0, 0]), np.array([0, 0, 1.0])]
    out = []
    for a in axes:
        cyl = _cylinder_for_axis(coords, sd, remaining, a, grid, contain_tol)
        if cyl is not None:
            out.append(cyl)
    return out


def _box_candidate(coords, sd, remaining, grid, contain_tol):
    """Oriented (PCA) box, shrunk along each axis until it sits inside the solid."""
    pts = coords[remaining]
    if len(pts) < 8:
        return None
    c, vh = _principal_axes(pts)
    ax, ay, az = vh[0], vh[1], vh[2]
    local = np.column_stack([(pts - c) @ ax, (pts - c) @ ay, (pts - c) @ az])
    half = np.array([np.abs(local[:, 0]).max(),
                     np.abs(local[:, 1]).max(),
                     np.abs(local[:, 2]).max()])
    # Shrink uniformly until the box surface is contained (cheap, robust enough).
    for _ in range(12):
        box = FitResult(kind="BOX", rms=0.0, max_error=0.0,
                        params={"center": c, "ax": ax, "ay": ay, "az": az,
                                "hx": float(half[0]), "hy": float(half[1]),
                                "hz": float(half[2])})
        if _contained(box, grid, contain_tol):
            if min(half) <= grid.spacing:
                return None
            return box
        half *= 0.9
    return None


# --- containment & coverage ----------------------------------------------------

def _contained(prim, grid, tol):
    """True if the primitive's surface lies inside the solid (sd ≥ -tol)."""
    pts = _surface_samples(prim)
    return bool(np.all(grid.sample(pts) >= -tol))


def _surface_samples(prim):
    """A modest set of points on the primitive surface, for the containment test."""
    p = prim.params
    if prim.kind == "SPHERE":
        u = np.linspace(0, np.pi, 7)
        v = np.linspace(0, 2 * np.pi, 12, endpoint=False)
        uu, vv = np.meshgrid(u, v)
        d = np.stack([np.sin(uu) * np.cos(vv), np.sin(uu) * np.sin(vv), np.cos(uu)], -1)
        return np.asarray(p["center"]) + p["radius"] * d.reshape(-1, 3)
    if prim.kind == "CYLINDER":
        axis = np.asarray(p["axis"]); axis = axis / np.linalg.norm(axis)
        e1, e2 = _basis(axis)
        ang = np.linspace(0, 2 * np.pi, 16, endpoint=False)
        zs = np.linspace(-0.5, 0.5, 6) * p["height"]
        ring = p["radius"] * (np.outer(np.cos(ang), e1) + np.outer(np.sin(ang), e2))
        pts = [np.asarray(p["base"]) + z * axis + ring for z in zs]
        # include cap rims (already covered) — fine
        return np.vstack(pts)
    if prim.kind == "BOX":
        ax, ay, az = (np.asarray(p[k]) for k in ("ax", "ay", "az"))
        hx, hy, hz = p["hx"], p["hy"], p["hz"]
        s = np.array([-1.0, 1.0])
        corners = []
        for sx in s:
            for sy in s:
                for sz in s:
                    corners.append(np.asarray(p["center"]) + sx * hx * ax + sy * hy * ay + sz * hz * az)
        # face centres too
        for a, h in ((ax, hx), (ay, hy), (az, hz)):
            corners.append(np.asarray(p["center"]) + h * a)
            corners.append(np.asarray(p["center"]) - h * a)
        return np.array(corners)
    return np.zeros((0, 3))


def _basis(axis):
    seed = np.array([1.0, 0, 0]) if abs(axis[0]) < 0.9 else np.array([0, 1.0, 0])
    e1 = np.cross(axis, seed); e1 /= np.linalg.norm(e1)
    e2 = np.cross(axis, e1)
    return e1, e2


def _voxels_in(prim, coords):
    """Boolean mask over the grid: which voxel centres fall inside the primitive."""
    p = prim.params
    X = coords.reshape(-1, 3)
    if prim.kind == "SPHERE":
        m = np.linalg.norm(X - np.asarray(p["center"]), axis=1) <= p["radius"]
    elif prim.kind == "CYLINDER":
        axis = np.asarray(p["axis"]); axis = axis / np.linalg.norm(axis)
        rel = X - np.asarray(p["base"])
        w = rel @ axis
        rho = np.linalg.norm(rel - np.outer(w, axis), axis=1)
        m = (rho <= p["radius"]) & (np.abs(w) <= 0.5 * p["height"])
    elif prim.kind == "BOX":
        ax, ay, az = (np.asarray(p[k]) for k in ("ax", "ay", "az"))
        rel = X - np.asarray(p["center"])
        m = ((np.abs(rel @ ax) <= p["hx"]) & (np.abs(rel @ ay) <= p["hy"])
             & (np.abs(rel @ az) <= p["hz"]))
    else:
        m = np.zeros(len(X), dtype=bool)
    return m.reshape(coords.shape[:3])


# --- the greedy cover ----------------------------------------------------------

def fit_solids(grid: SDFGrid, *, max_primitives=16, min_cover_frac=0.01,
               contain_tol=None, progress=None):
    """Greedily cover the solid's interior with a union of inscribed primitives.

    Returns ``(results, coverage)`` — a list of ADD-role :class:`FitResult` solids
    and the fraction of interior volume they cover.
    """
    inside = grid.sd > 0
    total = int(inside.sum())
    if total == 0:
        return [], 0.0
    coords = grid.coords()
    covered = np.zeros_like(inside)
    tol = contain_tol if contain_tol is not None else 0.75 * grid.spacing
    scale = region_scale(coords[inside])

    results = []
    for _ in range(max_primitives):
        remaining = inside & ~covered
        if int(remaining.sum()) / total < min_cover_frac:
            break
        cands = []
        sph = _sphere_candidate(coords, grid.sd, remaining)
        if sph is not None:
            cands.append(sph)
        cands += _cylinder_candidates(coords, grid.sd, remaining, grid, tol)
        box = _box_candidate(coords, grid.sd, remaining, grid, tol)
        if box is not None:
            cands.append(box)

        best = None
        for prim in cands:
            mask = _voxels_in(prim, coords)
            gain = int((mask & remaining).sum())
            if best is None or gain > best[0]:
                best = (gain, prim, mask)
        if best is None or best[0] / total < min_cover_frac:
            break
        gain, prim, mask = best
        covered |= mask
        prim.params["_scale"] = scale
        prim.summary = summarize(prim.kind, prim.params)
        results.append(prim)
        if progress is not None:
            progress(min(0.95, 0.2 + 0.7 * len(results) / max_primitives))

    return results, int(covered.sum()) / total
