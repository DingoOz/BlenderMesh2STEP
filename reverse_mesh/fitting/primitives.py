# SPDX-License-Identifier: GPL-3.0-or-later
"""Least-squares fitters for the analytic primitives.

Each ``fit_*`` takes a :class:`Region` (world-space vertices plus paired face
centroids/normals) and returns a :class:`FitResult`, or ``None`` if the region
is too small/degenerate. Residuals are point-to-surface distances in world units.
"""

from __future__ import annotations

import numpy as np

from .common import (
    FitResult,
    Region,
    best_plane_normal,
    fit_circle_2d,
    orthonormal_basis,
    region_scale,
    residual_stats,
    smallest_singular_axis,
    snap_value,
)


def summarize(kind: str, p: dict) -> str:
    """One-line human summary of a fit from its params — the single source of
    truth shared by the fitters and by :func:`snap_result` (so a snapped fit's
    summary stays in the same format)."""
    if kind == "PLANE":
        n = p["normal"]
        return f"Plane · n=({n[0]:.3f}, {n[1]:.3f}, {n[2]:.3f})"
    if kind == "SPHERE":
        return f"Sphere · r={p['radius']:.4g}"
    if kind == "CYLINDER":
        return f"Cylinder · r={p['radius']:.4g} · h={p['height']:.4g}"
    if kind == "CONE":
        return (f"Cone · r1={p['radius1']:.3g} r2={p['radius2']:.3g} · "
                f"{np.degrees(p['half_angle']):.1f}°")
    if kind == "TORUS":
        return f"Torus · R={p['major_radius']:.4g} r={p['minor_radius']:.4g}"
    if kind == "BOX":
        return f"Box · {2 * p['hx']:.3g}×{2 * p['hy']:.3g}×{2 * p['hz']:.3g}"
    return kind


# Which params of each kind are snappable lengths/radii (see :func:`snap_result`).
_LENGTH_KEYS = {
    "PLANE": ("half_u", "half_v"),
    "BOX": ("hx", "hy", "hz"),
    "CYLINDER": ("radius", "height"),
    "CONE": ("radius1", "radius2", "height"),
    "SPHERE": ("radius",),
    "TORUS": ("major_radius", "minor_radius"),
}


def snap_result(result: FitResult, step=None, preferred=None,
                rel_tol=None) -> tuple[FitResult, bool]:
    """Snap a fit's dimensions to 'nice' values in place; return (result, changed).

    Mutates the length/radius params via :func:`snap_value`, keeps the cone's
    ``half_angle`` consistent with its snapped radii, and regenerates ``summary``.
    """
    if result is None:
        return result, False
    kw = {} if rel_tol is None else {"rel_tol": rel_tol}
    changed = False
    for key in _LENGTH_KEYS.get(result.kind, ()):
        if key in result.params:
            v, ch = snap_value(float(result.params[key]), step, preferred, **kw)
            if ch:
                result.params[key] = v
                changed = True
    if changed:
        if result.kind == "CONE":
            p = result.params
            h = float(p.get("height", 0.0))
            if h > 1e-12:
                p["half_angle"] = float(np.arctan(abs(p["radius2"] - p["radius1"]) / h))
        result.summary = summarize(result.kind, result.params)
    return result, changed


def fit_plane(region: Region) -> FitResult | None:
    points = region.points
    if len(points) < 3:
        return None
    centroid, normal = best_plane_normal(points)

    # Orient the normal to agree with the mesh's face normals.
    if len(region.face_normals) and np.dot(region.face_normals.mean(axis=0), normal) < 0:
        normal = -normal

    signed = (points - centroid) @ normal
    rms, max_err = residual_stats(signed)

    # In-plane extent, so the generated quad matches the selected region.
    e1, e2 = orthonormal_basis(normal)
    rel = points - centroid
    half_u = float(np.max(np.abs(rel @ e1))) if len(rel) else 1.0
    half_v = float(np.max(np.abs(rel @ e2))) if len(rel) else 1.0

    params = {
        "point": centroid,
        "normal": normal,
        "e1": e1,
        "e2": e2,
        "half_u": half_u,
        "half_v": half_v,
        "_scale": region_scale(points),
    }
    return FitResult(kind="PLANE", rms=rms, max_error=max_err, params=params,
                     summary=summarize("PLANE", params))


def fit_sphere(region: Region) -> FitResult | None:
    points = region.points
    if len(points) < 4:
        return None
    # Algebraic fit: |p|^2 = 2c·p + (r^2 - |c|^2). Linear in (c, d).
    a_mat = np.column_stack([2.0 * points, np.ones(len(points))])
    rhs = np.sum(points ** 2, axis=1)
    sol, *_ = np.linalg.lstsq(a_mat, rhs, rcond=None)
    center = sol[:3]
    radius = float(np.sqrt(max(sol[3] + center @ center, 1e-18)))

    radial = np.linalg.norm(points - center, axis=1) - radius
    rms, max_err = residual_stats(radial)

    params = {"center": center, "radius": radius, "_scale": region_scale(points)}
    return FitResult(kind="SPHERE", rms=rms, max_error=max_err, params=params,
                     summary=summarize("SPHERE", params))


def fit_cylinder(region: Region) -> FitResult | None:
    points = region.points
    normals = region.face_normals
    if len(points) < 6:
        return None

    # Axis: the direction the surface normals avoid (they lie perpendicular to it).
    # Fall back to the point cloud's long axis when normals are unavailable.
    if len(normals) >= 3 and np.any(normals):
        axis = smallest_singular_axis(normals)
    else:
        _, axis = best_plane_normal(points)

    e1, e2 = orthonormal_basis(axis)
    origin = points.mean(axis=0)
    rel = points - origin
    uv = np.column_stack([rel @ e1, rel @ e2])

    center2d, radius, radial = fit_circle_2d(uv)
    rms, max_err = residual_stats(radial)

    # Axial extent → cylinder height and midpoint along the axis.
    w = rel @ axis
    w_min, w_max = float(w.min()), float(w.max())
    height = w_max - w_min
    center3d = (
        origin
        + e1 * center2d[0]
        + e2 * center2d[1]
        + axis * ((w_min + w_max) / 2.0)
    )

    params = {
        "base": center3d,      # midpoint of the axial span, on the axis
        "axis": axis,
        "radius": radius,
        "height": height,
        "_scale": region_scale(points),
    }
    return FitResult(kind="CYLINDER", rms=rms, max_error=max_err, params=params,
                     summary=summarize("CYLINDER", params))


def fit_cone(region: Region) -> FitResult | None:
    """Cone fit via the apex condition.

    Every point on a cone satisfies ``(p - apex) · n = 0`` (the surface contains
    the line to the apex, which is orthogonal to the normal). That is *linear* in
    the apex, so we solve it directly by least squares — far more robust than
    approximating the axis location by a centroid. This needs point/normal pairs,
    so it uses the face centroids and face normals (not the loose vertices).
    """
    pts = region.face_points
    normals = region.face_normals
    if len(normals) < 4 or len(pts) != len(normals) or not np.any(normals):
        return None

    # Apex: solve n_i · apex = n_i · p_i for all faces.
    rhs = np.sum(normals * pts, axis=1)
    apex, *_ = np.linalg.lstsq(normals, rhs, rcond=None)

    # Cone normals satisfy n·axis = const → they lie on a plane whose normal is
    # the cone axis. Orient it to point from apex toward the sampled region.
    _, axis = best_plane_normal(normals)
    if np.dot(pts.mean(axis=0) - apex, axis) < 0:
        axis = -axis

    # Measure the fit against the true surface vertices for an honest residual.
    surf = region.points if len(region.points) >= 6 else pts
    v = surf - apex                         # apex-relative position vectors
    w = v @ axis                            # axial coordinate (distance from apex)
    perp = np.linalg.norm(v - np.outer(w, axis), axis=1)  # radial distance

    # On an ideal cone, perp = w * tan(half_angle) through the origin (the apex).
    denom = float(np.dot(w, w))
    slope = float(np.dot(w, perp) / denom) if denom > 1e-18 else 0.0
    if abs(slope) < 1e-9:
        return None  # effectively a cylinder; let fit_cylinder handle it.

    residual = perp - slope * w
    rms, max_err = residual_stats(residual)

    w_min, w_max = float(w.min()), float(w.max())
    r1 = abs(slope * w_min)               # radius at the near (w_min) end
    r2 = abs(slope * w_max)               # radius at the far (w_max) end
    height = w_max - w_min
    base3d = apex + axis * w_min          # on the axis, at the r1 end
    half_angle = float(np.arctan(abs(slope)))

    params = {
        "base": base3d,        # on the axis, at the r1 (w_min) end
        "axis": axis,
        "radius1": r1,
        "radius2": r2,
        "height": height,
        "apex": apex,
        "half_angle": half_angle,
        "_scale": region_scale(surf),
    }
    return FitResult(kind="CONE", rms=rms, max_error=max_err, params=params,
                     summary=summarize("CONE", params))


def _torus_on_axis(points: np.ndarray, center: np.ndarray, axis: np.ndarray):
    """Given an axis and centre, solve for the torus radii by linear LSQ.

    From ``(rho - R)^2 + w^2 = r^2`` ⇒ ``rho^2 + w^2 = 2R·rho + (r^2 - R^2)``,
    which is linear in ``R`` and ``k = r^2 - R^2``. The centre is taken as the
    region centroid — exact for a regularly-tessellated full ring (the real-mesh
    case), and unbiased otherwise. Returns ``(R, r, signed_distances)``.
    """
    axis = _unit(axis)
    d = points - center
    w = d @ axis
    rho = np.linalg.norm(d - np.outer(w, axis), axis=1)

    a_mat = np.column_stack([2.0 * rho, np.ones(len(rho))])
    rhs = rho ** 2 + w ** 2
    sol, *_ = np.linalg.lstsq(a_mat, rhs, rcond=None)
    big_r = float(sol[0])
    r_tube = float(np.sqrt(max(sol[1] + big_r ** 2, 1e-18)))

    dist = np.sqrt((rho - big_r) ** 2 + w ** 2) - r_tube
    return big_r, r_tube, dist


def fit_torus(region: Region) -> FitResult | None:
    """Torus fit via PCA axis initialisation + local angular refinement.

    A torus has 7 DOF and no closed-form fit. We seed the axis from the
    least-spread (PCA) direction — correct for a full ring — then refine it with
    a shrinking local grid search, solving the radii linearly at each candidate.
    Best on substantial/complete rings; partial patches (e.g. small fillet arcs)
    are approximate because the centre is taken as the centroid.
    """
    points = region.points
    if len(points) < 10:
        return None

    center = points.mean(axis=0)
    _, axis = best_plane_normal(points)        # least-spread direction = ring axis

    def score(ax):
        ax = _unit(ax)
        big_r, r_tube, dist = _torus_on_axis(points, center, ax)
        return float(np.sqrt(np.mean(dist ** 2))), ax, big_r, r_tube

    best = score(axis)
    # Two refinement passes, each searching a shrinking cone around the best axis.
    for max_ang, n_ring in ((np.radians(20), 12), (np.radians(5), 12)):
        e1, e2 = orthonormal_basis(best[1])
        for theta in np.linspace(max_ang / 4, max_ang, 4):
            t = np.tan(theta)
            for phi in np.linspace(0.0, 2 * np.pi, n_ring, endpoint=False):
                cand = best[1] + t * (np.cos(phi) * e1 + np.sin(phi) * e2)
                s = score(cand)
                if s[0] < best[0]:
                    best = s

    axis, big_r, r_tube = best[1], best[2], best[3]
    if big_r <= 1e-9 or r_tube <= 1e-9:
        return None
    _, _, dist = _torus_on_axis(points, center, axis)
    rms, max_err = residual_stats(dist)

    params = {
        "center": center,
        "axis": axis,
        "major_radius": big_r,
        "minor_radius": r_tube,
        "_scale": region_scale(points),
    }
    return FitResult(kind="TORUS", rms=rms, max_error=max_err, params=params,
                     summary=summarize("TORUS", params))


def _cluster_axes(normals, tol_cos=0.966):
    """Return the dominant ± normal directions (box face axes), most-supported first.

    A box's normals fall into three ±clusters; the normal covariance is isotropic
    for a cube (degenerate eigenvalues), so clustering — not eigen-decomposition —
    is what recovers a *rotated* box's orientation.
    """
    n = normals / np.clip(np.linalg.norm(normals, axis=1, keepdims=True), 1e-12, None)
    # Fold ±d onto one hemisphere so opposite faces share a direction.
    k = np.argmax(np.abs(n), axis=1)
    signs = np.sign(n[np.arange(len(n)), k])
    signs[signs == 0] = 1
    folded = n * signs[:, None]

    remaining = folded
    axes = []
    while len(remaining) and len(axes) < 3:
        # Subsample candidate directions for the support search (keeps it O(m)).
        step = max(1, len(remaining) // 200)
        cand = remaining[::step]
        counts = (np.abs(cand @ remaining.T) > tol_cos).sum(axis=1)
        d = cand[int(np.argmax(counts))]
        mask = np.abs(remaining @ d) > tol_cos
        cluster = remaining[mask]
        cs = np.sign(cluster @ d)
        cs[cs == 0] = 1
        axes.append(_unit((cluster * cs[:, None]).mean(axis=0)))
        remaining = remaining[~mask]
    return axes


def fit_box(region: Region) -> FitResult | None:
    """Oriented box (cuboid) fit from the face normals.

    A box's faces have normals along three orthogonal axes, recovered by
    clustering the face normals. Extents come from the vertex span along each
    axis — so this yields the actual (possibly rotated) box, not the meaningless
    single average a plane fit would give over a whole cube.
    """
    points = region.points
    normals = region.face_normals
    if len(points) < 4 or len(normals) < 3 or not np.any(normals):
        return None

    axes = _cluster_axes(normals)
    if len(axes) < 2:
        return None  # not enough distinct face directions to define a box

    # Orthonormalise: trust the two best-supported clusters, derive the third.
    ax = _unit(axes[0])
    ay = axes[1] - np.dot(axes[1], ax) * ax
    if np.linalg.norm(ay) < 1e-6:
        return None
    ay = _unit(ay)
    az = _unit(np.cross(ax, ay))
    ay = _unit(np.cross(az, ax))

    # A real box has (almost) every face normal aligned to one of its 3 axes.
    # This rejects shapes whose points merely *lie on* a box surface but whose
    # normals don't agree — e.g. a cylinder's two end rings sit on a box's caps.
    nu = normals / np.clip(np.linalg.norm(normals, axis=1, keepdims=True), 1e-12, None)
    axn = np.array([ax, ay, az])
    best_align = np.max(np.abs(nu @ axn.T), axis=1)
    if np.mean(best_align > 0.985) < 0.8:        # <80% of faces axis-aligned
        return None

    cx, cy, cz = points @ ax, points @ ay, points @ az
    lo = np.array([cx.min(), cy.min(), cz.min()])
    hi = np.array([cx.max(), cy.max(), cz.max()])
    half = (hi - lo) / 2.0
    cloc = (hi + lo) / 2.0
    center = ax * cloc[0] + ay * cloc[1] + az * cloc[2]

    scale = region_scale(points)
    if half.min() < 1e-3 * scale:          # degenerate (planar/linear) → not a box
        return None

    # Residual: distance from each vertex to the nearest face plane (0 on surface).
    local = np.column_stack([cx - cloc[0], cy - cloc[1], cz - cloc[2]])
    surf_dist = np.min(half - np.abs(local), axis=1)
    rms, max_err = residual_stats(surf_dist)

    params = {
        "center": center,
        "ax": ax, "ay": ay, "az": az,
        "hx": float(half[0]), "hy": float(half[1]), "hz": float(half[2]),
        "_scale": scale,
    }
    return FitResult(kind="BOX", rms=rms, max_error=max_err, params=params,
                     summary=summarize("BOX", params))


def predicted_normals(result: FitResult, pts: np.ndarray) -> np.ndarray:
    """Unit surface normals the fitted primitive predicts at ``pts``.

    Used to disambiguate fits that match the *points* equally well but imply
    different *normals* (e.g. two rings of points fit both a cylinder and a
    sphere — only their normals differ).
    """
    if result.kind == "BOX":
        p = result.params
        axes = [np.asarray(p["ax"]), np.asarray(p["ay"]), np.asarray(p["az"])]
        half = [p["hx"], p["hy"], p["hz"]]
        center = np.asarray(p["center"])
        out = np.zeros((len(pts), 3))
        local = np.column_stack([(pts - center) @ a for a in axes])
        ratios = np.abs(local) / np.array(half)
        which = np.argmax(ratios, axis=1)          # nearest face axis per point
        for i, k in enumerate(which):
            out[i] = np.sign(local[i, k]) * axes[k]
        return out
    p = result.params
    if result.kind == "PLANE":
        return np.tile(_unit(p["normal"]), (len(pts), 1))
    if result.kind == "SPHERE":
        d = pts - p["center"]
        return d / np.clip(np.linalg.norm(d, axis=1, keepdims=True), 1e-12, None)
    if result.kind == "CYLINDER":
        rel = pts - p["base"]
        w = rel @ p["axis"]
        rho = rel - np.outer(w, p["axis"])
        return rho / np.clip(np.linalg.norm(rho, axis=1, keepdims=True), 1e-12, None)
    if result.kind == "CONE":
        rel = pts - p["apex"]
        w = rel @ p["axis"]
        rho = rel - np.outer(w, p["axis"])
        rho = rho / np.clip(np.linalg.norm(rho, axis=1, keepdims=True), 1e-12, None)
        ca, sa = np.cos(p["half_angle"]), np.sin(p["half_angle"])
        n = ca * rho - sa * p["axis"]
        return n / np.clip(np.linalg.norm(n, axis=1, keepdims=True), 1e-12, None)
    if result.kind == "TORUS":
        axis = p["axis"]
        d = pts - p["center"]
        w = d @ axis
        radial = d - np.outer(w, axis)
        rho = np.clip(np.linalg.norm(radial, axis=1, keepdims=True), 1e-12, None)
        # Spine point = centre + R·radial_unit; normal points from spine to p.
        n = (radial / rho) * (rho - p["major_radius"]) + np.outer(w, axis)
        return n / np.clip(np.linalg.norm(n, axis=1, keepdims=True), 1e-12, None)
    raise ValueError(result.kind)


def signed_distances(result: FitResult, pts: np.ndarray) -> np.ndarray:
    """Per-point signed residual the fitted primitive implies at ``pts``.

    The sibling of :func:`predicted_normals`: where that returns the predicted
    *normal* per point, this returns the *deviation* per point. It reproduces the
    exact residual each fitter measures internally — the codebase otherwise keeps
    only the aggregate ``(rms, max_error)`` — so ``rms(signed_distances(r, r_pts))``
    recovers ``r.rms``. Used by the fit-quality heatmap and the RANSAC wrapper.
    """
    p = result.params
    pts = np.asarray(pts, dtype=float)
    if result.kind == "PLANE":
        return (pts - np.asarray(p["point"])) @ _unit(np.asarray(p["normal"]))
    if result.kind == "SPHERE":
        return np.linalg.norm(pts - np.asarray(p["center"]), axis=1) - p["radius"]
    if result.kind == "CYLINDER":
        rel = pts - np.asarray(p["base"])
        axis = _unit(np.asarray(p["axis"]))
        w = rel @ axis
        rho = np.linalg.norm(rel - np.outer(w, axis), axis=1)
        return rho - p["radius"]
    if result.kind == "CONE":
        rel = pts - np.asarray(p["apex"])
        axis = _unit(np.asarray(p["axis"]))
        w = rel @ axis
        perp = np.linalg.norm(rel - np.outer(w, axis), axis=1)
        slope = float(np.tan(p["half_angle"]))
        return perp - slope * w           # matches fit_cone's radial residual
    if result.kind == "TORUS":
        _, _, dist = _torus_on_axis(pts, np.asarray(p["center"]), np.asarray(p["axis"]))
        return dist
    if result.kind == "BOX":
        axes = [_unit(np.asarray(p["ax"])), _unit(np.asarray(p["ay"])), _unit(np.asarray(p["az"]))]
        half = np.array([p["hx"], p["hy"], p["hz"]])
        local = np.column_stack([(pts - np.asarray(p["center"])) @ a for a in axes])
        return np.min(half - np.abs(local), axis=1)   # matches fit_box's surf_dist
    raise ValueError(result.kind)


def _unit(v):
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-12 else v


def normal_alignment(result: FitResult, region: Region) -> float:
    """Mean |cos angle| between predicted and actual face normals (1 = perfect)."""
    if not len(region.face_normals) or not np.any(region.face_normals):
        return 1.0  # no normals to judge by; don't penalise.
    pred = predicted_normals(result, region.face_points)
    dots = np.abs(np.sum(pred * region.face_normals, axis=1))
    return float(np.mean(dots))


# Registry used by the operator for the AUTO path and explicit selection.
FITTERS = {
    "PLANE": fit_plane,
    "BOX": fit_box,
    "CYLINDER": fit_cylinder,
    "CONE": fit_cone,
    "SPHERE": fit_sphere,
    "TORUS": fit_torus,
}


# Faces must agree with the predicted surface normal at least this well for a
# fit to be trusted in AUTO mode.
_ALIGNMENT_GATE = 0.9

# A fit this good (relative RMS) is considered "essentially exact".
_GOOD_FIT = 1e-3

# Occam tie-break: when several primitives all fit well, prefer the simpler,
# more CAD-likely one. (A two-ring band fits both a cylinder and a sphere — the
# cylinder is the simpler explanation.)
_PREFERENCE = {"PLANE": 0, "BOX": 1, "CYLINDER": 2, "CONE": 3, "SPHERE": 4, "TORUS": 5}


def fit_auto(region: Region, return_candidates: bool = False):
    """Try every primitive and return the best fit.

    Selection order:
      1. Drop fits whose predicted normals disagree with the face normals.
      2. Among fits that are essentially exact, prefer the simplest primitive.
      3. Otherwise take the lowest relative RMS.

    With ``return_candidates=True`` returns ``(best, candidates)`` where
    ``candidates`` is every successful fit annotated with its agreement metrics,
    sorted best-first (winner, then ascending relative RMS) — for surfacing the
    runner-ups and the tie-break margin in the UI. ``best`` is ``None`` only when
    no primitive fit at all.
    """
    candidates = []
    for fitter in FITTERS.values():
        try:
            res = fitter(region)
        except np.linalg.LinAlgError:
            res = None
        if res is not None:
            candidates.append(res)
    if not candidates:
        return (None, []) if return_candidates else None

    aligned = [r for r in candidates if normal_alignment(r, region) >= _ALIGNMENT_GATE]
    pool = aligned if aligned else candidates

    good = [r for r in pool if r.rel_rms < _GOOD_FIT]
    if good:
        best = min(good, key=lambda r: _PREFERENCE[r.kind])
    else:
        best = min(pool, key=lambda r: r.rel_rms)

    if not return_candidates:
        return best

    annotated = [
        {
            "kind": r.kind,
            "rel_rms": r.rel_rms,
            "alignment": normal_alignment(r, region),
            "gated": r in pool,
            "winner": r is best,
            "result": r,
        }
        for r in candidates
    ]
    annotated.sort(key=lambda c: (not c["winner"], c["rel_rms"]))
    return best, annotated
