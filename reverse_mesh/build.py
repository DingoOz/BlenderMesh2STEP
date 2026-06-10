# SPDX-License-Identifier: GPL-3.0-or-later
"""Turn a :class:`FitResult` into a clean, analytic Blender mesh object.

The fitters work in world space, so we generate each primitive in a canonical
local frame (axis = +Z) and place it with ``object.matrix_world``. Tessellation
here is purely cosmetic — the *parameters* on the object's custom properties are
the exact analytic truth, ready for a later STEP/OCCT export pass.
"""

from __future__ import annotations

import math

import bpy
import numpy as np
from mathutils import Matrix, Vector


def generate_mesh(kind, params, segments: int = 48):
    """Generate ``(verts, faces, matrix)`` for a primitive in its canonical frame.

    ``params`` is the kind-specific schema (world-space points/dirs/lengths) used
    throughout the add-on; ``matrix`` places the canonical-frame geometry in world
    space. Shared by :func:`build_object` and the forward-build rebuild path.
    """
    if kind == "PLANE":
        return _plane(params)
    if kind == "BOX":
        return _box(params)
    if kind == "SPHERE":
        return _sphere(params, segments)
    if kind == "CYLINDER":
        return _cylinder(params, segments)
    if kind == "CONE":
        return _cone(params, segments)
    if kind == "TORUS":
        return _torus(params, segments)
    if kind == "FILLET":
        return _fillet(params, segments)
    raise ValueError(f"Unknown primitive kind: {kind}")


def mesh_fingerprint(mesh) -> str:
    """Cheap content fingerprint of a mesh, for drift detection.

    Built primitives are small (a few hundred to ~1200 verts), so hashing every
    vertex is fine. Coordinates are rounded so float noise from a round-trip
    through ``from_pydata`` doesn't register as an edit.
    """
    import hashlib

    h = hashlib.sha1()
    h.update(f"{len(mesh.vertices)}:{len(mesh.polygons)}".encode())
    for v in mesh.vertices:
        h.update(f"{v.co.x:.6f},{v.co.y:.6f},{v.co.z:.6f};".encode())
    return h.hexdigest()


def build_object(context, result, segments: int = 48, operation: str = "ADD",
                 cut_mode: str = "THROUGH"):
    """Create and link a new mesh object representing ``result``.

    Returns the created object. Exact fit parameters are stashed under the
    object's ``["reverse"]`` custom-property dict for downstream tooling.
    ``operation`` ("ADD"/"SUBTRACT") is the boolean role for OCCT export.
    """
    kind = result.kind
    p = result.params

    verts, faces, matrix = generate_mesh(kind, p, segments)

    mesh = bpy.data.meshes.new(f"Reverse_{kind.title()}")
    mesh.from_pydata([tuple(v) for v in verts], [], faces)
    mesh.update()

    obj = bpy.data.objects.new(f"Reverse_{kind.title()}", mesh)
    obj.matrix_world = matrix
    params = _serialise_params(kind, p, result)
    # Record the placement so STEP export can follow later manual moves.
    params["_xform"] = [matrix[i][j] for i in range(4) for j in range(4)]
    params["_fingerprint"] = mesh_fingerprint(mesh)
    params["op"] = operation
    params["cut"] = cut_mode
    obj["reverse"] = params
    # Tint cutters red in the viewport (Object colour shading) as a hint.
    obj.color = (0.85, 0.25, 0.25, 1.0) if operation == "SUBTRACT" else (0.8, 0.8, 0.8, 1.0)
    context.collection.objects.link(obj)
    return obj


# --- canonical-frame generators ------------------------------------------------

def _axis_matrix(axis, location) -> Matrix:
    """World matrix mapping local +Z onto ``axis`` and origin onto ``location``."""
    z = Vector((float(axis[0]), float(axis[1]), float(axis[2]))).normalized()
    rot = Vector((0.0, 0.0, 1.0)).rotation_difference(z).to_matrix().to_4x4()
    return Matrix.Translation(Vector((float(location[0]), float(location[1]), float(location[2])))) @ rot


def _plane(p):
    """Single quad centred on the fit point, oriented to the fit normal."""
    hu, hv = p["half_u"], p["half_v"]
    verts = [(-hu, -hv, 0.0), (hu, -hv, 0.0), (hu, hv, 0.0), (-hu, hv, 0.0)]
    faces = [(0, 1, 2, 3)]
    # Build a frame from the stored in-plane basis so the quad aligns with the region.
    e1 = Vector(tuple(float(x) for x in p["e1"]))
    e2 = Vector(tuple(float(x) for x in p["e2"]))
    n = Vector(tuple(float(x) for x in p["normal"]))
    loc = Vector(tuple(float(x) for x in p["point"]))
    rot = Matrix((e1, e2, n)).transposed().to_4x4()
    return verts, faces, Matrix.Translation(loc) @ rot


def _box(p):
    """Oriented cuboid: 8 corners, 6 quad faces, placed by its axis frame."""
    hx, hy, hz = p["hx"], p["hy"], p["hz"]
    verts = [
        (-hx, -hy, -hz), (hx, -hy, -hz), (hx, hy, -hz), (-hx, hy, -hz),
        (-hx, -hy, hz), (hx, -hy, hz), (hx, hy, hz), (-hx, hy, hz),
    ]
    faces = [
        (0, 3, 2, 1), (4, 5, 6, 7),          # -Z, +Z
        (0, 1, 5, 4), (2, 3, 7, 6),          # -Y, +Y
        (1, 2, 6, 5), (0, 4, 7, 3),          # +X, -X
    ]
    ax = Vector(tuple(float(x) for x in p["ax"]))
    ay = Vector(tuple(float(x) for x in p["ay"]))
    az = Vector(tuple(float(x) for x in p["az"]))
    loc = Vector(tuple(float(x) for x in p["center"]))
    rot = Matrix((ax, ay, az)).transposed().to_4x4()
    return verts, faces, Matrix.Translation(loc) @ rot


def _ring(radius, z, segments):
    return [
        (radius * math.cos(2 * math.pi * i / segments),
         radius * math.sin(2 * math.pi * i / segments),
         z)
        for i in range(segments)
    ]


def _cylinder(p, segments):
    r, h = p["radius"], p["height"]
    bottom = _ring(r, -h / 2.0, segments)
    top = _ring(r, h / 2.0, segments)
    verts = bottom + top
    faces = []
    for i in range(segments):
        j = (i + 1) % segments
        faces.append((i, j, segments + j, segments + i))   # side quad
    faces.append(tuple(range(segments)))                    # bottom cap (ngon)
    faces.append(tuple(range(2 * segments - 1, segments - 1, -1)))  # top cap
    return verts, faces, _axis_matrix(p["axis"], p["base"])


def _cone(p, segments):
    r1, r2, h = p["radius1"], p["radius2"], p["height"]
    # base() is at the w_min (r1) end; generate from 0..h along +Z.
    bottom = _ring(max(r1, 1e-6), 0.0, segments)
    top = _ring(max(r2, 1e-6), h, segments)
    verts = bottom + top
    faces = []
    for i in range(segments):
        j = (i + 1) % segments
        faces.append((i, j, segments + j, segments + i))
    faces.append(tuple(range(segments)))
    faces.append(tuple(range(2 * segments - 1, segments - 1, -1)))
    return verts, faces, _axis_matrix(p["axis"], p["base"])


def _sphere(p, segments):
    rings = max(8, segments // 2)
    segs = max(8, segments)
    r = p["radius"]
    verts = []
    for ri in range(1, rings):           # exclude poles, added explicitly
        theta = math.pi * ri / rings
        for si in range(segs):
            phi = 2 * math.pi * si / segs
            verts.append((
                r * math.sin(theta) * math.cos(phi),
                r * math.sin(theta) * math.sin(phi),
                r * math.cos(theta),
            ))
    north = len(verts); verts.append((0.0, 0.0, r))
    south = len(verts); verts.append((0.0, 0.0, -r))

    faces = []
    for ri in range(rings - 2):
        for si in range(segs):
            sj = (si + 1) % segs
            a = ri * segs + si
            b = ri * segs + sj
            c = (ri + 1) * segs + sj
            d = (ri + 1) * segs + si
            faces.append((a, b, c, d))
    for si in range(segs):               # pole fans
        sj = (si + 1) % segs
        faces.append((north, si, sj))
        base = (rings - 2) * segs
        faces.append((south, base + sj, base + si))

    loc = Vector(tuple(float(x) for x in p["center"]))
    return verts, faces, Matrix.Translation(loc)


def _torus(p, segments):
    """Torus around local +Z: major circle in XY, tube of radius minor_radius."""
    major_segs = max(12, segments)
    minor_segs = max(8, segments // 2)
    big_r, r = p["major_radius"], p["minor_radius"]
    verts = []
    for i in range(major_segs):
        u = 2 * math.pi * i / major_segs
        cu, su = math.cos(u), math.sin(u)
        for j in range(minor_segs):
            v = 2 * math.pi * j / minor_segs
            rr = big_r + r * math.cos(v)
            verts.append((rr * cu, rr * su, r * math.sin(v)))
    faces = []
    for i in range(major_segs):
        for j in range(minor_segs):
            a = i * minor_segs + j
            b = i * minor_segs + (j + 1) % minor_segs
            c = ((i + 1) % major_segs) * minor_segs + (j + 1) % minor_segs
            d = ((i + 1) % major_segs) * minor_segs + j
            faces.append((a, b, c, d))
    return verts, faces, _axis_matrix(p["axis"], p["center"])


def _fillet(p, segments):
    """A partial cylinder strip (the edge-fillet patch) in its local axis/ref frame."""
    r, h = p["radius"], p["height"]
    u0, u1 = p["u_min"], p["u_max"]
    n = max(8, segments // 2)
    bottom, top = [], []
    for i in range(n + 1):
        u = u0 + (u1 - u0) * i / n
        x, y = r * math.cos(u), r * math.sin(u)
        bottom.append((x, y, -h / 2.0))
        top.append((x, y, h / 2.0))
    verts = bottom + top
    faces = [(i, i + 1, n + 1 + i + 1, n + 1 + i) for i in range(n)]
    az = Vector(tuple(float(x) for x in p["axis"])).normalized()
    e1 = Vector(tuple(float(x) for x in p["ref"]))
    e1 = (e1 - e1.dot(az) * az).normalized()
    e2 = az.cross(e1)
    loc = Vector(tuple(float(x) for x in p["base"]))
    rot = Matrix((e1, e2, az)).transposed().to_4x4()
    return verts, faces, Matrix.Translation(loc) @ rot


def _serialise_params(kind, p, result):
    """Flatten fit params to plain floats/lists for object custom properties.

    Geometry params are numeric (floats or float lists). String/bool *metadata*
    params (e.g. ``role``, ``thread_spec``, ``hole_preset``) are passed through
    verbatim so they round-trip back out at export — see the metadata allowlist in
    :func:`reverse_mesh.operators._feature_from_object`.
    """
    out = {"kind": kind, "rms": float(result.rms), "max_error": float(result.max_error)}
    for key, value in p.items():
        if key.startswith("_"):
            continue
        if isinstance(value, np.ndarray):
            out[key] = [float(x) for x in value]
        elif isinstance(value, (list, tuple)):
            out[key] = [float(x) for x in value]
        elif isinstance(value, str):
            out[key] = value                 # metadata string — keep as-is
        elif isinstance(value, bool):
            out[key] = bool(value)
        else:
            out[key] = float(value)
    return out
