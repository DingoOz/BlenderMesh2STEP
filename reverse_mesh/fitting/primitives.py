# SPDX-License-Identifier: GPL-3.0-or-later
"""Least-squares fitters for the analytic primitives.

Each ``fit_*`` takes a :class:`Region` (world-space vertices plus paired face
centroids/normals) and returns a :class:`FitResult`, or ``None`` if the region
is too small/degenerate. Residuals are point-to-surface distances in world units.
"""

from __future__ import annotations

import numpy as np

from . import profile as profile2d
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
    if kind == "EXTRUDE":
        rows = np.asarray(p["profile"], dtype=float)
        n_arc = int(np.sum(rows[:, 0] == profile2d.ARC))
        n_line = len(rows) - n_arc
        segs = f"{n_line}L" + (f"+{n_arc}A" if n_arc else "")
        return f"Extrude · {segs} · h={p['height']:.4g}"
    if kind == "REVOLVE":
        rows = np.asarray(p["profile"], dtype=float)
        n_arc = int(np.sum(rows[:, 0] == profile2d.ARC))
        n_line = len(rows) - n_arc
        segs = f"{n_line}L" + (f"+{n_arc}A" if n_arc else "")
        r_max = float(np.max(rows[:, [1, 3]]))
        return f"Revolve · {segs} · r≤{r_max:.4g}"
    return kind


# Which params of each kind are snappable lengths/radii (see :func:`snap_result`).
_LENGTH_KEYS = {
    "PLANE": ("half_u", "half_v"),
    "BOX": ("hx", "hy", "hz"),
    "CYLINDER": ("radius", "height"),
    "CONE": ("radius1", "radius2", "height"),
    "SPHERE": ("radius",),
    "TORUS": ("major_radius", "minor_radius"),
    "EXTRUDE": ("height",),
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

    # In-plane extent, so the generated quad matches the selected region. Align the
    # axes to the region's principal in-plane directions (not arbitrary world-seeded
    # ones), then size the quad to the actual min/max along each axis. An
    # axis-arbitrary bounding box over an elongated or diagonal patch is far larger
    # than the region — its empty corners jut out past the true surface.
    e1, e2 = orthonormal_basis(normal)
    rel = points - centroid
    inplane = rel - np.outer(rel @ normal, normal)
    if len(points) >= 3:
        try:
            _, _, vh = np.linalg.svd(inplane, full_matrices=False)
            a1 = vh[0] - (vh[0] @ normal) * normal
            if np.linalg.norm(a1) > 1e-9:
                e1 = a1 / np.linalg.norm(a1)
                e2 = np.cross(normal, e1)
        except np.linalg.LinAlgError:
            pass
    u = rel @ e1
    v = rel @ e2
    if len(rel):
        umin, umax = float(u.min()), float(u.max())
        vmin, vmax = float(v.min()), float(v.max())
    else:
        umin = umax = vmin = vmax = 0.0
    half_u = max((umax - umin) / 2.0, 1e-9)
    half_v = max((vmax - vmin) / 2.0, 1e-9)
    # Centre the quad on the region's bounding rectangle, not the centroid (which a
    # one-sided patch can sit off-centre of). Stays in the fitted plane.
    point = centroid + e1 * ((umin + umax) / 2.0) + e2 * ((vmin + vmax) / 2.0)

    params = {
        "point": point,
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
    # Degeneracy guard: points lying on ≤2 circles about the axis (e.g. a
    # prism's two vertex rings) leave the torus underdetermined — a whole
    # family of tori passes through them exactly, so an rms of 0 means
    # nothing. Require at least 3 distinct (rho, |w|) circles.
    scale = region_scale(points)
    d = points - center
    wv = d @ axis
    rho = np.linalg.norm(d - np.outer(wv, axis), axis=1)
    q = max(1e-6 * scale, 1e-12)
    circles = {(round(float(r_) / q), round(abs(float(w_)) / q))
               for r_, w_ in zip(rho, wv)}
    if len(circles) < 3:
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


def fit_fillet(region: Region) -> FitResult | None:
    """Edge fillet (constant-radius rolling-ball blend) → a *partial* cylinder.

    A fillet between two faces is a slice of a cylinder: the surface normals sweep
    an arc rather than a full circle. The radius and axis come from the same
    circle/axis fit as :func:`fit_cylinder` (the algebraic circle fit works on an
    arc), and additionally we recover the *angular extent* of the arc — the
    largest empty angular gap is the missing part — so the surface can be exported
    as a trimmed patch. Returns ``None`` if the points actually wrap most of a full
    circle (that's a cylinder, not a fillet).
    """
    points = region.points
    normals = region.face_normals
    if len(points) < 6:
        return None

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

    # Angular extent about the fitted centre: the arc is everything but the
    # single largest empty gap between consecutive (sorted) point angles.
    ang = np.sort(np.arctan2(uv[:, 1] - center2d[1], uv[:, 0] - center2d[0]))
    ext = np.concatenate([ang, [ang[0] + 2 * np.pi]])
    gaps = np.diff(ext)
    gi = int(np.argmax(gaps))
    if gi == len(ang) - 1:                      # widest gap straddles ±π
        u_min, u_max = float(ang[0]), float(ang[-1])
    else:
        u_min, u_max = float(ang[gi + 1]), float(ang[gi] + 2 * np.pi)
    span = u_max - u_min
    if span > np.radians(330.0):                # nearly closed → it's a cylinder
        return None

    w = rel @ axis
    w_min, w_max = float(w.min()), float(w.max())
    height = w_max - w_min
    center3d = (origin + e1 * center2d[0] + e2 * center2d[1]
                + axis * ((w_min + w_max) / 2.0))

    params = {
        "base": center3d,          # on the axis, at the axial midpoint
        "axis": axis,
        "ref": e1,                 # u = 0 reference direction
        "radius": radius,
        "height": height,
        "u_min": u_min,            # arc start angle from ref (radians)
        "u_max": u_max,            # arc end angle (u_max > u_min)
        "_scale": region_scale(points),
    }
    return FitResult(kind="FILLET", rms=rms, max_error=max_err, params=params,
                     summary=f"Fillet · r={radius:.4g} · {np.degrees(span):.0f}°")


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


# Per-face classification tolerances for the prism (extrude) fit: a face is a
# cap when |n·axis| exceeds _EXT_CAP_COS, a side when it is below _EXT_SIDE_COS;
# anything in between means the region is not a clean prism.
_EXT_CAP_COS = 0.999
_EXT_SIDE_COS = 0.02


def _extrude_on_axis(region: Region, axis: np.ndarray, nu: np.ndarray, scale: float):
    """Try to reconstruct a prism along ``axis``; None if the region isn't one.

    Returns ``(axis, loop, t_min, height)`` with ``loop`` the ordered 2D
    profile vertices in the :func:`orthonormal_basis` frame of ``axis``.
    """
    points = region.points
    fverts = region.face_verts
    align = np.abs(nu @ axis)
    is_cap = align > _EXT_CAP_COS
    is_side = align < _EXT_SIDE_COS
    if not np.all(is_cap | is_side) or not np.any(is_side):
        return None

    t = points @ axis
    t_min, t_max = float(t.min()), float(t.max())
    height = t_max - t_min
    if height < 1e-6 * scale:
        return None
    # Cap faces must actually sit at the ends.
    if len(region.face_points) == len(fverts):
        ft = region.face_points @ axis
        at_end = np.minimum(np.abs(ft - t_min), np.abs(ft - t_max)) < 1e-4 * scale
        if not np.all(at_end[is_cap]):
            return None

    e1, e2 = orthonormal_basis(axis)
    uv_all = np.column_stack([points @ e1, points @ e2])

    # Each side face must project to exactly two distinct profile points — the
    # signature of a face ruled along the extrusion axis.
    tol = 1e-6 * scale
    segs = []
    for fi in np.nonzero(is_side)[0]:
        pts2 = uv_all[np.asarray(fverts[fi], dtype=int)]
        distinct = [pts2[0]]
        for q in pts2[1:]:
            if all(np.linalg.norm(q - d) > tol for d in distinct):
                distinct.append(q)
        if len(distinct) != 2:
            return None
        segs.append((tuple(distinct[0]), tuple(distinct[1])))

    loops = profile2d.chain_segments(segs, tol)
    if len(loops) != 1:
        return None                       # open, non-manifold, or has holes
    return axis, loops[0], t_min, height


def _extrude_frame(p):
    """Orthonormal (e1, e2, axis) from stored extrude params."""
    axis = _unit(np.asarray(p["axis"], dtype=float))
    e1 = np.asarray(p["xdir"], dtype=float)
    e1 = _unit(e1 - np.dot(e1, axis) * axis)
    e2 = np.cross(axis, e1)
    return e1, e2, axis


def fit_extrude(region: Region) -> FitResult | None:
    """Extruded planar profile (prism) fit: axis + height + line/arc profile.

    Needs per-face vertex indices (``region.face_verts``) to reconstruct the
    profile boundary, so it only participates when the region was built from a
    real mesh. The prism gate is strict: every face must be either a cap
    (normal ∥ axis) or a side *ruled along the axis* (its vertices project to
    exactly two distinct profile points), and the profile must chain into one
    closed loop. Full-circle profiles are rejected — that's a cylinder.
    """
    points = region.points
    normals = region.face_normals
    fverts = region.face_verts
    if fverts is None or len(fverts) < 5 or len(points) < 6:
        return None
    if len(normals) != len(fverts) or not np.any(normals):
        return None

    scale = region_scale(points)

    # Axis candidates: every distinct face-normal direction (a cap normal is the
    # axis) plus the least-represented direction of the normal set (exact when
    # the selection is side-walls only). Score = how far each face normal is
    # from being either ∥ or ⊥ to the candidate.
    nu = normals / np.clip(np.linalg.norm(normals, axis=1, keepdims=True), 1e-12, None)
    cands = [smallest_singular_axis(nu)]
    seen = []
    for n in nu[:: max(1, len(nu) // 64)]:
        if not any(abs(float(n @ s)) > 0.999 for s in seen):
            seen.append(n)
            cands.append(n)

    def score(d):
        a = np.abs(nu @ d)
        return float(np.mean(np.minimum(a, 1.0 - a) ** 2))

    # A rectilinear prism satisfies the ∥/⊥ normal criterion along *several*
    # directions (an L-bracket scores perfectly along its profile axes too), so
    # try the candidates in score order and keep the first whose profile
    # actually reconstructs: side faces ruled along the axis, one closed loop.
    cands.sort(key=score)
    picked = None
    for axis in cands:
        if score(axis) > 1e-6:
            break                          # candidates are sorted; rest are worse
        got = _extrude_on_axis(region, axis, nu, scale)
        if got is not None:
            picked = got
            break
    if picked is None:
        return None
    axis, loop, t_min, height = picked

    e1, e2 = orthonormal_basis(axis)
    prof = profile2d.segment_loop(loop, scale)
    if prof is None:
        return None                       # no corners → a full circle → cylinder

    # Frame origin: on the bottom plane, at the loop centroid (keeps profile
    # coordinates small and the object origin inside the part).
    c2 = loop.mean(axis=0)
    base = e1 * c2[0] + e2 * c2[1] + axis * t_min
    prof = prof.copy()
    prof[:, 1:7:2] -= c2[0]               # sx, ex, cx
    prof[:, 2:7:2] -= c2[1]               # sy, ey, cy
    # Guard: the recentring must keep LINE rows' unused center columns at zero.
    prof[prof[:, 0] == profile2d.LINE, 5:7] = 0.0

    # Residual: every vertex lies on a side wall (2D distance to the boundary)
    # or on a cap plane (axial distance to an end).
    t = points @ axis
    uv_c = np.column_stack([points @ e1, points @ e2]) - c2
    d2d = profile2d.distance_to_profile(prof, uv_c)
    d_cap = np.minimum(np.abs(t - t_min), np.abs(t - (t_min + height)))
    resid = np.minimum(d2d, d_cap)
    rms, max_err = residual_stats(resid)

    params = {
        "base": base,
        "axis": axis,
        "xdir": e1,
        "height": height,
        "profile": prof,
        "_scale": scale,
    }
    return FitResult(kind="EXTRUDE", rms=rms, max_error=max_err, params=params,
                     summary=summarize("EXTRUDE", params))


def fit_revolve(region: Region) -> FitResult | None:
    """Solid of revolution: axis + a closed line/arc profile in (radius, z).

    The axis comes from a linear (Plücker) solve — every surface normal of a
    revolved surface, taken as a line, intersects the axis, which is linear in
    the axis line's 6 Plücker coordinates. The profile is reconstructed like
    the extrude's: every face of a revolved tessellation projects to exactly
    two distinct (rho, w) stations, giving segments that chain into a closed
    half-plane loop (closed along the axis itself when the solid touches it).

    REVOLVE is deliberately not part of AUTO: every quadric is a surface of
    revolution, so it would shadow the simpler primitives. Pick it explicitly.
    Returns None for full-circle profiles (that's a torus).
    """
    points = region.points
    normals = region.face_normals
    fverts = region.face_verts
    if fverts is None or len(fverts) < 4 or len(points) < 8:
        return None
    if len(normals) != len(fverts) or not np.any(normals):
        return None
    scale = region_scale(points)

    # Plücker axis solve: line (d, m) with m = a×d meets the normal line at
    # p_i with direction n_i iff  n_i·m + (p_i×n_i)·d = 0  — linear in (m, d).
    fp = region.face_points if len(region.face_points) == len(fverts) else None
    if fp is None:
        return None
    nu = normals / np.clip(np.linalg.norm(normals, axis=1, keepdims=True), 1e-12, None)
    # Sample the normal line at every face *vertex*, not the centroid: on a
    # two-station lathe tessellation all centroid normal lines pass exactly
    # through the body centre (wall centroids sit on the mid-plane, cap
    # centroids on the axis), collapsing the system to a 3-D null space in
    # which the axis direction is arbitrary. Vertices sit off the mid-plane
    # and break that concurrency. Centre the moment terms for conditioning.
    vp = []
    vn = []
    for fi, idxs in enumerate(fverts):
        for vi in idxs:
            vp.append(points[int(vi)])
            vn.append(nu[fi])
    vp = np.asarray(vp)
    vn = np.asarray(vn)
    c0 = vp.mean(axis=0)
    a_mat = np.column_stack([vn, np.cross(vp - c0, vn)])
    try:
        _, sv, vh = np.linalg.svd(a_mat, full_matrices=False)
    except np.linalg.LinAlgError:
        return None
    sol = vh[-1]
    m_vec, d_vec = sol[:3], sol[3:]
    dn = float(np.linalg.norm(d_vec))
    if dn < 1e-9:
        return None                       # no finite axis direction
    axis = d_vec / dn
    # Point on the axis (closest to c0), back in world coordinates.
    a_pt = np.cross(axis, m_vec / dn) + c0

    # (rho, w) half-plane projection.
    rel = points - a_pt
    wv = rel @ axis
    rho = np.linalg.norm(rel - np.outer(wv, axis), axis=1)
    uv_all = np.column_stack([rho, wv])

    # Every face must project to exactly two distinct stations (ruled along
    # the rotation) — quads of a lathe tessellation and pole triangles do.
    tol = 1e-5 * scale
    segs = []
    for fi in range(len(fverts)):
        pts2 = uv_all[np.asarray(fverts[fi], dtype=int)]
        distinct = [pts2[0]]
        for q in pts2[1:]:
            if all(np.linalg.norm(q - dpt) > tol for dpt in distinct):
                distinct.append(q)
        if len(distinct) == 1:
            # Every vertex on one latitude circle: an n-gon cap disc (it has no
            # centre vertex). Only a face ⊥ axis can span that disc.
            if abs(float(nu[fi] @ axis)) < 0.99:
                return None
            segs.append(((0.0, float(distinct[0][1])), tuple(distinct[0])))
        elif len(distinct) == 2:
            segs.append((tuple(distinct[0]), tuple(distinct[1])))
        else:
            return None

    loops = profile2d.chain_segments(segs, tol)
    if not loops:
        # A solid touching the axis has an unclosed profile whose two loose
        # ends sit at rho ≈ 0 — close it along the axis.
        ends = _open_chain_ends(segs, tol)
        if ends is None or any(e[0] > 10 * tol for e in ends):
            return None
        segs.append((ends[0], ends[1]))
        loops = profile2d.chain_segments(segs, tol)
    if len(loops) != 1:
        return None
    prof = profile2d.segment_loop(loops[0], scale)
    if prof is None:
        return None                       # full circle → that's a torus

    d2d = profile2d.distance_to_profile(prof, uv_all)
    rms, max_err = residual_stats(d2d)

    params = {
        "base": a_pt,
        "axis": axis,
        "profile": prof,
        "_scale": scale,
    }
    return FitResult(kind="REVOLVE", rms=rms, max_error=max_err, params=params,
                     summary=summarize("REVOLVE", params))


def _open_chain_ends(segs, tol):
    """The two degree-1 endpoints of an almost-closed segment set, or None.

    Duplicate segments (a lathe emits each profile segment once per
    revolution step) are collapsed before counting degrees, mirroring
    :func:`profile2d.chain_segments`.
    """
    def q(p):
        return (round(p[0] / tol), round(p[1] / tol))

    uniq = {}
    for a, b in segs:
        key = frozenset((q(a), q(b)))
        if len(key) == 2:
            uniq[key] = (a, b)
    counts = {}
    coords = {}
    for a, b in uniq.values():
        for p in (a, b):
            counts[q(p)] = counts.get(q(p), 0) + 1
            coords[q(p)] = p
    ends = [coords[k] for k, c in counts.items() if c == 1]
    return (ends[0], ends[1]) if len(ends) == 2 else None


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
    if result.kind == "EXTRUDE":
        e1, e2, axis = _extrude_frame(p)
        rel = pts - np.asarray(p["base"], dtype=float)
        t = rel @ axis
        h = float(p["height"])
        uv = np.column_stack([rel @ e1, rel @ e2])
        prof = np.asarray(p["profile"], dtype=float)
        d2d = profile2d.distance_to_profile(prof, uv)
        # Nearer to an end plane than to the side wall → a cap face (normal ∥
        # axis; sign is irrelevant to the |cos| alignment metric).
        d_cap = np.minimum(np.abs(t), np.abs(t - h))
        n2 = profile2d.outward_normals(prof, uv)
        out = np.outer(n2[:, 0], e1) + np.outer(n2[:, 1], e2)
        out[d_cap < d2d] = axis
        return out
    if result.kind == "REVOLVE":
        axis = _unit(np.asarray(p["axis"], dtype=float))
        rel = pts - np.asarray(p["base"], dtype=float)
        wv = rel @ axis
        radial = rel - np.outer(wv, axis)
        rn = np.clip(np.linalg.norm(radial, axis=1, keepdims=True), 1e-12, None)
        radial_u = radial / rn
        uv = np.column_stack([rn[:, 0], wv])
        n2 = profile2d.outward_normals(np.asarray(p["profile"], dtype=float), uv)
        out = radial_u * n2[:, :1] + np.outer(n2[:, 1], axis)
        return out / np.clip(np.linalg.norm(out, axis=1, keepdims=True), 1e-12, None)
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
    if result.kind in ("CYLINDER", "FILLET"):
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
    if result.kind == "EXTRUDE":
        e1, e2, axis = _extrude_frame(p)
        rel = pts - np.asarray(p["base"], dtype=float)
        t = rel @ axis
        h = float(p["height"])
        uv = np.column_stack([rel @ e1, rel @ e2])
        d2d = profile2d.distance_to_profile(np.asarray(p["profile"], dtype=float), uv)
        d_cap = np.minimum(np.abs(t), np.abs(t - h))
        return np.minimum(d2d, d_cap)      # matches fit_extrude's residual
    if result.kind == "REVOLVE":
        axis = _unit(np.asarray(p["axis"], dtype=float))
        rel = pts - np.asarray(p["base"], dtype=float)
        wv = rel @ axis
        rho = np.linalg.norm(rel - np.outer(wv, axis), axis=1)
        uv = np.column_stack([rho, wv])
        return profile2d.distance_to_profile(np.asarray(p["profile"], dtype=float), uv)
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
    "EXTRUDE": fit_extrude,
    "REVOLVE": fit_revolve,
}

# Kinds excluded from AUTO: every quadric is a surface of revolution, so
# REVOLVE would shadow the simpler primitives — it must be chosen explicitly
# (FILLET is likewise explicit-only, and is not in FITTERS at all).
_AUTO_EXCLUDED = {"REVOLVE"}


# Faces must agree with the predicted surface normal at least this well for a
# fit to be trusted in AUTO mode.
_ALIGNMENT_GATE = 0.9

# Face *centroids* must also lie near the surface (relative RMS) for a fit to
# be trusted in AUTO mode. Vertices alone can coincide with a surface the mesh
# does not represent — a hexagonal prism's 12 vertices lie exactly on a sphere
# — but the sphere fails at the face centroids, while genuine tessellated
# surfaces keep their centroid sag well inside this bound.
_FACE_RESIDUAL_GATE = 0.02

# A fit this good (relative RMS) is considered "essentially exact".
_GOOD_FIT = 1e-3


def _face_residual(result: FitResult, region: Region) -> float:
    """Relative RMS of the fitted surface at the region's face centroids."""
    if not len(region.face_points):
        return 0.0
    try:
        d = signed_distances(result, region.face_points)
    except ValueError:
        return 0.0
    scale = result.params.get("_scale", 1.0) or 1.0
    return float(np.sqrt(np.mean(d ** 2))) / scale

# Occam tie-break: when several primitives all fit well, prefer the simpler,
# more CAD-likely one. (A two-ring band fits both a cylinder and a sphere — the
# cylinder is the simpler explanation.)
# EXTRUDE sits last: it is the most general shape, so any canonical primitive
# that also fits exactly (box, cylinder) is the better statement of intent.
_PREFERENCE = {"PLANE": 0, "BOX": 1, "CYLINDER": 2, "CONE": 3, "SPHERE": 4,
               "TORUS": 5, "EXTRUDE": 6}


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
    for kind, fitter in FITTERS.items():
        if kind in _AUTO_EXCLUDED:
            continue
        try:
            res = fitter(region)
        except np.linalg.LinAlgError:
            res = None
        if res is not None:
            candidates.append(res)
    if not candidates:
        return (None, []) if return_candidates else None

    aligned = [r for r in candidates
               if normal_alignment(r, region) >= _ALIGNMENT_GATE
               and _face_residual(r, region) <= _FACE_RESIDUAL_GATE]
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


# Points/faces beyond this fraction of the region size are outliers. A fit this
# clean already (relative RMS) is left untouched — RANSAC only kicks in when the
# plain fit is poor, preserving machine-precision fits on clean selections.
RANSAC_REL_THRESHOLD = 0.02
_RANSAC_CLEAN = 1e-3
_RANSAC_ITERS = 80
_RANSAC_SAMPLE = 14          # small sample → decent odds of an outlier-free draw


def _trim_to_inliers(region, result, thr):
    """Region restricted to points/faces within ``thr`` of the fitted surface."""
    p_mask = np.abs(signed_distances(result, region.points)) <= thr
    if len(region.face_points):
        f_mask = np.abs(signed_distances(result, region.face_points)) <= thr
        fp = region.face_points[f_mask]
        fn = region.face_normals[f_mask]
    else:
        f_mask = np.ones(0, dtype=bool)
        fp, fn = region.face_points, region.face_normals
    return Region(points=region.points[p_mask], face_points=fp, face_normals=fn), p_mask, f_mask


def fit_robust(region: Region, fitter, rel_threshold=RANSAC_REL_THRESHOLD,
               iters=_RANSAC_ITERS, seed=0) -> FitResult | None:
    """Outlier-tolerant fit via RANSAC consensus, then a clean refit.

    A few stray triangles in a selection (a chamfer, an N-gon that triangulated
    oddly) drag a plain least-squares fit off — and trimming can't recover, since
    the corrupted fit mis-ranks which points are outliers. Instead this fits many
    small random samples, keeps the model with the most inliers (within
    ``rel_threshold × region size``), and refits on that consensus set. Rejected
    faces show up red in the fit-quality heatmap.

    A selection that already fits cleanly is returned unchanged, so the
    machine-precision behaviour on clean meshes is never disturbed.
    """
    base = fitter(region)
    if base is None:
        return None
    if base.rel_rms < _RANSAC_CLEAN:
        return base                                  # already excellent; no outliers

    n_pts = len(region.points)
    n_faces = len(region.face_points)
    thr = rel_threshold * region_scale(region.points)
    rng = np.random.default_rng(seed)
    s_pts = min(n_pts, _RANSAC_SAMPLE)
    s_faces = min(n_faces, _RANSAC_SAMPLE) if n_faces else 0

    best_count, best_cand = -1, None
    for _ in range(iters):
        pid = rng.choice(n_pts, size=s_pts, replace=False)
        if n_faces:
            fid = rng.choice(n_faces, size=s_faces, replace=False)
            sub = Region(points=region.points[pid],
                         face_points=region.face_points[fid],
                         face_normals=region.face_normals[fid])
        else:
            sub = Region(points=region.points[pid],
                         face_points=region.face_points, face_normals=region.face_normals)
        cand = fitter(sub)
        if cand is None:
            continue
        count = int((np.abs(signed_distances(cand, region.points)) <= thr).sum())
        if count > best_count:
            best_count, best_cand = count, cand

    # No usable consensus, or the best model already keeps everything (no outliers).
    if best_cand is None or best_count >= n_pts:
        return base

    # Trim the full region (points AND faces) by the consensus model, then refit
    # cleanly on the inliers — so outlier normals can't corrupt the final axis.
    trimmed, p_mask, _ = _trim_to_inliers(region, best_cand, thr)
    if len(trimmed.points) >= 3:
        final = fitter(trimmed)
        if final is not None:
            return final
    return base
