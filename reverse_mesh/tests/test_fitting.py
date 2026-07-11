# SPDX-License-Identifier: GPL-3.0-or-later
"""Standalone tests for the fitting core — no Blender required.

Run from the extension root:
    python3 -m tests.test_fitting
or with pytest:
    pytest reverse_mesh/tests
"""

import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fitting import (  # noqa: E402
    Region,
    fit_auto,
    fit_box,
    fit_cone,
    fit_cylinder,
    fit_fillet,
    fit_plane,
    fit_robust,
    fit_sphere,
    fit_torus,
    signed_distances,
    snap_result,
)
from fitting.common import deviation_color, snap_value  # noqa: E402
from fitting.patterns import classify_arrangement, match_cylinders  # noqa: E402


def _region(pts, nrm):
    return Region.from_points(pts, nrm)


def _sample_plane(n=400, seed=0):
    rng = np.random.default_rng(seed)
    uv = rng.uniform(-5, 5, size=(n, 2))
    pts = np.column_stack([uv[:, 0], uv[:, 1], np.full(n, 2.0)])  # z = 2 plane
    normals = np.tile([0.0, 0.0, 1.0], (n, 1))
    return pts, normals


def _sample_sphere(r=3.0, n=600, seed=1):
    rng = np.random.default_rng(seed)
    v = rng.normal(size=(n, 3))
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    center = np.array([1.0, -2.0, 0.5])
    return center + r * v, v


def _sample_cylinder(r=2.0, h=10.0, n=800, seed=2):
    rng = np.random.default_rng(seed)
    theta = rng.uniform(0, 2 * math.pi, n)
    z = rng.uniform(0, h, n)
    pts = np.column_stack([r * np.cos(theta), r * np.sin(theta), z])
    normals = np.column_stack([np.cos(theta), np.sin(theta), np.zeros(n)])
    return pts, normals


def _sample_cone(r1=4.0, half_angle_deg=20.0, h=8.0, n=900, seed=3):
    rng = np.random.default_rng(seed)
    slope = math.tan(math.radians(half_angle_deg))
    theta = rng.uniform(0, 2 * math.pi, n)
    z = rng.uniform(0, h, n)
    r = r1 + slope * z
    pts = np.column_stack([r * np.cos(theta), r * np.sin(theta), z])
    # Outward normal of a cone tilts by the half-angle.
    ca, sa = math.cos(math.radians(half_angle_deg)), math.sin(math.radians(half_angle_deg))
    normals = np.column_stack([ca * np.cos(theta), ca * np.sin(theta), -sa * np.ones(n)])
    normals /= np.linalg.norm(normals, axis=1, keepdims=True)
    return pts, normals


def _sample_torus(big_r=5.0, r=1.5, n_major=64, n_minor=24, seed=4):
    # Regular grid, like a real torus mesh (centroid = exact centre).
    u = np.linspace(0, 2 * math.pi, n_major, endpoint=False)
    v = np.linspace(0, 2 * math.pi, n_minor, endpoint=False)
    uu, vv = np.meshgrid(u, v)
    uu, vv = uu.ravel(), vv.ravel()
    rr = big_r + r * np.cos(vv)
    pts = np.column_stack([rr * np.cos(uu), rr * np.sin(uu), r * np.sin(vv)])
    # Outward normal points from the spine circle to the surface point.
    normals = np.column_stack([np.cos(vv) * np.cos(uu), np.cos(vv) * np.sin(uu), np.sin(vv)])
    center = np.array([2.0, -1.0, 0.5])
    return pts + center, normals


def _sample_fillet(r=1.5, span_deg=90.0, h=6.0, n=600, seed=7):
    # A quarter-cylinder strip: arc 0..span around +Z axis, radius r.
    rng = np.random.default_rng(seed)
    span = math.radians(span_deg)
    theta = rng.uniform(0.0, span, n)              # arc, not a full circle
    z = rng.uniform(0, h, n)
    pts = np.column_stack([r * np.cos(theta), r * np.sin(theta), z])
    normals = np.column_stack([np.cos(theta), np.sin(theta), np.zeros(n)])
    return pts, normals


def _sample_box(hx=2.0, hy=3.0, hz=4.0, seed=5, rot=True):
    # Sample points on the 6 faces of a (optionally rotated) box.
    rng = np.random.default_rng(seed)
    pts, nrm = [], []
    axes = np.eye(3)
    if rot:  # a fixed arbitrary rotation
        th = 0.7
        rz = np.array([[math.cos(th), -math.sin(th), 0],
                       [math.sin(th), math.cos(th), 0], [0, 0, 1]])
        axes = rz @ axes
    half = [hx, hy, hz]
    for k in range(3):
        for sgn in (-1, 1):
            uv = rng.uniform(-1, 1, size=(40, 2))
            o = [i for i in range(3) if i != k]
            local = np.zeros((40, 3))
            local[:, k] = sgn * half[k]
            local[:, o[0]] = uv[:, 0] * half[o[0]]
            local[:, o[1]] = uv[:, 1] * half[o[1]]
            pts.append(local @ axes.T)
            n = np.zeros((40, 3)); n[:, k] = sgn
            nrm.append(n @ axes.T)
    return np.vstack(pts), np.vstack(nrm)


def _check(name, ok, detail=""):
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name} {detail}")
    return ok



def _prism_region(loop2d, height, rot=None, offset=(0, 0, 0)):
    """Region for a prism over the closed CCW 2D loop: side quads + n-gon caps."""
    loop2d = np.asarray(loop2d, dtype=float)
    m = len(loop2d)
    rot = np.eye(3) if rot is None else np.asarray(rot, dtype=float)
    offset = np.asarray(offset, dtype=float)
    xf = lambda p: rot @ p + offset
    bottom = [xf(np.array([u, v, 0.0])) for u, v in loop2d]
    top = [xf(np.array([u, v, height])) for u, v in loop2d]
    points = np.array(bottom + top)
    fp, fn, fv = [], [], []
    for i in range(m):
        j = (i + 1) % m
        quad = [i, j, m + j, m + i]
        d = loop2d[j] - loop2d[i]
        n2 = np.array([d[1], -d[0], 0.0])
        n2 /= np.linalg.norm(n2)
        fp.append(points[quad].mean(axis=0))
        fn.append(rot @ n2)
        fv.append(tuple(quad))
    fp.append(np.array(bottom).mean(axis=0)); fn.append(rot @ np.array([0.0, 0.0, -1.0]))
    fv.append(tuple(range(m)))
    fp.append(np.array(top).mean(axis=0)); fn.append(rot @ np.array([0.0, 0.0, 1.0]))
    fv.append(tuple(range(m, 2 * m)))
    return Region(points=points, face_points=np.array(fp),
                  face_normals=np.array(fn), face_verts=fv)


def _rot(axis, angle):
    axis = np.asarray(axis, dtype=float); axis /= np.linalg.norm(axis)
    c, s = np.cos(angle), np.sin(angle)
    x, y, z = axis
    return np.array([[c + x*x*(1-c), x*y*(1-c) - z*s, x*z*(1-c) + y*s],
                     [y*x*(1-c) + z*s, c + y*y*(1-c), y*z*(1-c) - x*s],
                     [z*x*(1-c) - y*s, z*y*(1-c) + x*s, c + z*z*(1-c)]])


def _stadium(length=4.0, radius=1.0, arc_segs=16):
    pts = [(-length/2, -radius), (length/2, -radius)]
    for k in range(1, arc_segs):
        a = -np.pi/2 + np.pi * k / arc_segs
        pts.append((length/2 + radius*np.cos(a), radius*np.sin(a)))
    pts += [(length/2, radius), (-length/2, radius)]
    for k in range(1, arc_segs):
        a = np.pi/2 + np.pi * k / arc_segs
        pts.append((-length/2 + radius*np.cos(a), radius*np.sin(a)))
    return pts


def main():
    results = []

    pts, nrm = _sample_plane()
    r = fit_plane(_region(pts, nrm))
    results.append(_check("plane", r.rms < 1e-9 and abs(abs(r.params['normal'][2]) - 1) < 1e-6,
                          f"rms={r.rms:.2e}"))

    # Plane patch must be a TIGHT oriented rectangle (PCA-aligned), not an oversized
    # axis-aligned box whose empty corners jut outside the region. Elongated patch
    # rotated 35° in-plane: true half-extents are ~4.0 × ~0.6.
    _rng = np.random.default_rng(5)
    _u = _rng.uniform(-4, 4, 800)
    _v = _rng.uniform(-0.6, 0.6, 800)
    _a = math.radians(35)
    _x = _u * math.cos(_a) - _v * math.sin(_a)
    _y = _u * math.sin(_a) + _v * math.cos(_a)
    _pts = np.column_stack([_x, _y, np.zeros(800)])
    rp = fit_plane(_region(_pts, np.tile([0.0, 0.0, 1.0], (800, 1))))
    hu, hv = sorted([rp.params["half_u"], rp.params["half_v"]])
    results.append(_check("plane oriented extent", hu < 0.8 and 3.6 < hv < 4.2,
                          f"half-extents {hv:.2f} x {hu:.2f} (want ~4.0 x ~0.6)"))

    pts, nrm = _sample_sphere()
    r = fit_sphere(_region(pts, nrm))
    results.append(_check("sphere", r.rms < 1e-6 and abs(r.params['radius'] - 3.0) < 1e-4,
                          f"r={r.params['radius']:.4f} rms={r.rms:.2e}"))

    pts, nrm = _sample_cylinder()
    r = fit_cylinder(_region(pts, nrm))
    results.append(_check("cylinder", r.rms < 1e-6 and abs(r.params['radius'] - 2.0) < 1e-3,
                          f"r={r.params['radius']:.4f} h={r.params['height']:.3f} rms={r.rms:.2e}"))

    pts, nrm = _sample_cone()
    r = fit_cone(_region(pts, nrm))
    ok_cone = r is not None and r.rms < 1e-3
    results.append(_check("cone", ok_cone,
                          f"r1={r.params['radius1']:.3f} r2={r.params['radius2']:.3f} rms={r.rms:.2e}"
                          if r else "no fit"))

    pts, nrm = _sample_torus()
    r = fit_torus(_region(pts, nrm))
    ok_torus = (r is not None and r.rms < 1e-3
                and abs(r.params['major_radius'] - 5.0) < 1e-2
                and abs(r.params['minor_radius'] - 1.5) < 1e-2)
    results.append(_check("torus", ok_torus,
                          f"R={r.params['major_radius']:.4f} r={r.params['minor_radius']:.4f} rms={r.rms:.2e}"
                          if r else "no fit"))

    pts, nrm = _sample_box()
    r = fit_box(_region(pts, nrm))
    hs = sorted([r.params['hx'], r.params['hy'], r.params['hz']]) if r else []
    ok_box = r is not None and r.rms < 1e-9 and hs == sorted([2.0, 3.0, 4.0]) or (
        r is not None and r.rms < 1e-6 and
        all(abs(a - b) < 1e-3 for a, b in zip(sorted([r.params['hx'], r.params['hy'],
            r.params['hz']]), [2.0, 3.0, 4.0])))
    results.append(_check("box (rotated)", ok_box,
                          f"half={hs} rms={r.rms:.2e}" if r else "no fit"))

    # AUTO should pick the right kind on each clean sample.
    for name, sampler in [("plane", _sample_plane), ("sphere", _sample_sphere),
                          ("cylinder", _sample_cylinder), ("torus", _sample_torus),
                          ("box", _sample_box)]:
        pts, nrm = sampler()
        r = fit_auto(_region(pts, nrm))
        results.append(_check(f"auto->{name}", r is not None and r.kind == name.upper(),
                              f"got {r.kind if r else None}"))

    # signed_distances must reproduce each fitter's own RMS (INFRA-A): the
    # per-point residual evaluator and the internal aggregate must agree.
    for name, sampler, fitter in [("plane", _sample_plane, fit_plane),
                                  ("sphere", _sample_sphere, fit_sphere),
                                  ("cylinder", _sample_cylinder, fit_cylinder),
                                  ("cone", _sample_cone, fit_cone),
                                  ("torus", _sample_torus, fit_torus),
                                  ("box", _sample_box, fit_box)]:
        pts, nrm = sampler()
        r = fitter(_region(pts, nrm))
        d = signed_distances(r, pts)
        rms = float(np.sqrt(np.mean(d ** 2)))
        results.append(_check(f"signed_distances->{name}",
                              r is not None and abs(rms - r.rms) <= 1e-9 + 1e-6 * r.rms,
                              f"rms={rms:.3e} vs r.rms={r.rms:.3e}"))

    # fit_auto(return_candidates=True): back-compat + ordering (INFRA-D).
    pts, nrm = _sample_cylinder()
    best_only = fit_auto(_region(pts, nrm))
    best, cands = fit_auto(_region(pts, nrm), return_candidates=True)
    ok_cand = (best is not None and best.kind == "CYLINDER"
               and best_only.kind == best.kind            # single-return unchanged
               and len(cands) >= 1 and cands[0]["winner"]  # winner sorted first
               and cands[0]["result"] is best
               and all(cands[i]["rel_rms"] <= cands[i + 1]["rel_rms"] + 1e-12
                       for i in range(1, len(cands) - 1)))  # rest ascending
    results.append(_check("auto candidates", ok_cand,
                          f"n={len(cands)} top={cands[0]['kind'] if cands else None}"))

    # Dimension snapping (#3): close values snap, distant ones are left alone.
    v, ch = snap_value(19.98, step=1.0)               # 0.1% off → snap
    results.append(_check("snap close→nice", ch and abs(v - 20.0) < 1e-9, f"got {v}"))
    v, ch = snap_value(17.3, step=1.0)                # 1.7% off → keep
    results.append(_check("snap keeps genuine", (not ch) and abs(v - 17.3) < 1e-9, f"got {v}"))
    v, ch = snap_value(2.013, preferred=[1.0, 2.0, 2.5, 5.0])   # 0.65% off → snap
    results.append(_check("snap to preferred", ch and abs(v - 2.0) < 1e-9, f"got {v}"))

    # snap_result rounds a near-2.0 fitted radius and updates the summary.
    pts, nrm = _sample_cylinder(r=2.013, h=10.0)
    r = fit_cylinder(_region(pts, nrm))
    _, ch = snap_result(r, step=0.1)
    results.append(_check("snap_result cylinder", ch and abs(r.params['radius'] - 2.0) < 1e-9
                          and "r=2 " in (r.summary + " "), f"r={r.params['radius']} '{r.summary}'"))

    # Heatmap colour ramp (#1): green at 0, red at 1, clamped, yellow mid.
    g = deviation_color(0.0)
    r = deviation_color(1.0)
    y = deviation_color(0.5)
    over = deviation_color(5.0)
    ok_cmap = (g[0] < 0.01 and g[1] > 0.99            # green
               and r[0] > 0.99 and r[1] < 0.01        # red
               and y[0] > 0.99 and y[1] > 0.99        # yellow midpoint
               and over == r)                          # clamped above 1
    results.append(_check("deviation colour ramp", ok_cmap, f"g={g[:3]} r={r[:3]}"))

    # Robust fit (#13): stray outlier points must not corrupt the recovered radius.
    pts, nrm = _sample_cylinder(r=2.0, h=10.0, n=400)
    orng = np.random.default_rng(99)
    out_pts = orng.uniform(-6, 6, size=(40, 3)); out_pts[:, 0] += 9.0   # off to the side
    out_nrm = orng.normal(size=(40, 3)); out_nrm /= np.linalg.norm(out_nrm, axis=1, keepdims=True)
    P = np.vstack([pts, out_pts]); N = np.vstack([nrm, out_nrm])
    plain = fit_cylinder(_region(P, N))
    robust = fit_robust(_region(P, N), fit_cylinder, rel_threshold=0.05)
    err_plain = abs(plain.params["radius"] - 2.0)
    err_robust = abs(robust.params["radius"] - 2.0)
    results.append(_check("robust rejects outliers",
                          robust is not None and err_robust < 0.05 and err_robust < err_plain,
                          f"plain r={plain.params['radius']:.3f} robust r={robust.params['radius']:.3f}"))

    # Fillet (#5): a 90° quarter-cylinder strip → radius + arc span recovered.
    pts, nrm = _sample_fillet(r=1.5, span_deg=90.0)
    r = fit_fillet(_region(pts, nrm))
    span_deg = math.degrees(r.params["u_max"] - r.params["u_min"]) if r else 0.0
    results.append(_check("fillet edge", r is not None and r.rms < 1e-6
                          and abs(r.params["radius"] - 1.5) < 1e-3
                          and abs(span_deg - 90.0) < 8.0,
                          f"r={r.params['radius']:.4f} span={span_deg:.1f}° rms={r.rms:.2e}"
                          if r else "no fit"))
    # A near-full ring is a cylinder, not a fillet → declined.
    pts, nrm = _sample_cylinder()
    results.append(_check("fillet declines full ring", fit_fillet(_region(pts, nrm)) is None))

    # Pattern propagation (#8): 6 holes on a bolt circle, plus two decoys.
    bolt_r = 5.0
    seed = {"radius": 1.0, "axis": (0, 0, 1), "center": (bolt_r, 0, 0)}
    cands = []
    centers = []
    for k in range(6):
        a = 2 * math.pi * k / 6
        ctr = (bolt_r * math.cos(a), bolt_r * math.sin(a), 0.0)
        centers.append(ctr)
        cands.append({"radius": 1.0, "axis": (0, 0, 1), "center": ctr})
    cands.append({"radius": 2.5, "axis": (0, 0, 1), "center": (0, 0, 0)})     # wrong radius
    cands.append({"radius": 1.0, "axis": (1, 0, 0), "center": (0, 0, 3)})     # wrong axis
    matched = match_cylinders(seed, cands, radius_tol=0.05, axis_tol_deg=5.0)
    results.append(_check("pattern match", matched == [0, 1, 2, 3, 4, 5],
                          f"matched {matched}"))
    kind, info = classify_arrangement(centers, (0, 0, 1))
    results.append(_check("pattern circular", kind == "CIRCULAR" and info["count"] == 6
                          and abs(info["radius"] - bolt_r) < 1e-6, f"{kind} {info}"))
    lin_kind, _ = classify_arrangement([(0, 0, 0), (2, 0, 0), (4, 0, 0), (6, 0, 0)], (0, 0, 1))
    results.append(_check("pattern linear", lin_kind == "LINEAR", f"got {lin_kind}"))


    # Extrude (prism) fits: L-bracket (lines), rotated, stadium slot (arcs).
    from fitting import fit_extrude
    from fitting import profile as profile2d
    lshape = [(0, 0), (4, 0), (4, 1), (1, 1), (1, 3), (0, 3)]
    r = fit_extrude(_prism_region(lshape, 2.0))
    results.append(_check("extrude L prism", r is not None and r.kind == "EXTRUDE"
                          and r.rms < 1e-9 and abs(r.params["height"] - 2.0) < 1e-9
                          and len(r.params["profile"]) == 6,
                          r.summary if r else "None"))
    if r is not None:
        area = profile2d.profile_area(r.params["profile"])
        results.append(_check("extrude L area", abs(area - 6.0) < 1e-9, f"area {area}"))
    rr = fit_extrude(_prism_region(lshape, 2.0, rot=_rot([1, 2, 3], 0.7),
                                   offset=(5, -3, 2)))
    results.append(_check("extrude rotated L", rr is not None and rr.rms < 1e-9
                          and abs(abs(np.dot(rr.params["axis"],
                                             _rot([1, 2, 3], 0.7) @ [0, 0, 1])) - 1) < 1e-9,
                          rr.summary if rr else "None"))
    rs = fit_extrude(_prism_region(_stadium(), 1.5))
    ok = rs is not None and rs.rms < 1e-9
    if ok:
        prof = np.asarray(rs.params["profile"])
        n_arc = int(np.sum(prof[:, 0] == 1.0))
        area = profile2d.profile_area(prof)
        ok = n_arc == 2 and len(prof) == 4 and abs(area - (8 + math.pi)) < 1e-9
    results.append(_check("extrude stadium (2 lines + 2 arcs)", ok,
                          rs.summary if rs else "None"))
    # AUTO Occam: hexagon prism → EXTRUDE (its 12 vertices lie exactly on a
    # sphere, which the face-centroid gate must reject); cube → BOX;
    # 24-gon prism (intended cylinder) → CYLINDER; full circle → not EXTRUDE.
    hexa = [(math.cos(2*math.pi*i/6), math.sin(2*math.pi*i/6)) for i in range(6)]
    results.append(_check("AUTO hexagon prism → extrude",
                          fit_auto(_prism_region(hexa, 2.0)).kind == "EXTRUDE"))
    results.append(_check("AUTO cube → box",
                          fit_auto(_prism_region([(-1, -1), (1, -1), (1, 1), (-1, 1)],
                                                 2.0)).kind == "BOX"))
    poly24 = [(math.cos(2*math.pi*i/24), math.sin(2*math.pi*i/24)) for i in range(24)]
    results.append(_check("AUTO 24-gon prism → cylinder",
                          fit_auto(_prism_region(poly24, 2.0)).kind == "CYLINDER"))
    results.append(_check("extrude declines full circle",
                          fit_extrude(_prism_region(poly24, 2.0)) is None))
    # Regions without face-vertex topology can't fit an extrusion.
    pts, nrm = _sample_cylinder()
    results.append(_check("extrude needs topology",
                          fit_extrude(_region(pts, nrm)) is None))

    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
