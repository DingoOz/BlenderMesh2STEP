# SPDX-License-Identifier: GPL-3.0-or-later
"""Forward modeling: build objects directly from STEP-exportable primitives.

The reverse path *fits* primitives to an existing mesh; this module *creates*
them from user-typed dimensions. Both produce the same ``obj["reverse"]``
param schema, so the feature stack and the STEP exporters treat forward-built
objects identically to fitted ones (group tag ``"BUILD"`` aside).

PLANE and FILLET are deliberately not offered as forward building blocks:
they are open shells / trim patches in the exporters, not standalone solids.

The top of this module (``BUILD_KINDS``, ``PARAM_FIELDS``, :func:`make_params`,
:func:`make_result`) is importable without ``bpy`` so the pure-Python tests can
check schema completeness; only the rebuild/drift helpers need Blender.
"""

from __future__ import annotations

import math

try:
    from .fitting.common import FitResult
    from .fitting.primitives import summarize
except ImportError:                      # standalone (pure-Python tests)
    from fitting.common import FitResult
    from fitting.primitives import summarize

BUILD_KINDS = ("BOX", "CYLINDER", "CONE", "SPHERE", "TORUS", "EXTRUDE")

# Editable dimension fields per kind: (param key, UI label). Keys match the
# fitters' schemas / _PARAM_KINDS in operators.py and the reverse_build
# PropertyGroup in properties.py.
PARAM_FIELDS = {
    "BOX": (("hx", "Half X"), ("hy", "Half Y"), ("hz", "Half Z")),
    "CYLINDER": (("radius", "Radius"), ("height", "Height")),
    "CONE": (("radius1", "Base radius"), ("radius2", "Top radius"),
             ("height", "Height")),
    "SPHERE": (("radius", "Radius"),),
    "TORUS": (("major_radius", "Major radius"), ("minor_radius", "Minor radius")),
    # Forward-built extrusions are regular N-gon prisms: the side count is
    # fixed at creation; circumradius and height stay live-editable, and each
    # edit regenerates the stored profile (see refresh_extrude_profile).
    "EXTRUDE": (("radius", "Radius"), ("height", "Height")),
}


def make_params(kind: str, dims: dict, location) -> dict:
    """World-space param dict for ``kind`` centred on ``location``, axis = +Z.

    ``dims`` maps the kind's PARAM_FIELDS keys to floats; ``location`` is any
    3-sequence. Cylinders/cones get their ``base`` offset by −h/2 so the body
    is centred on the location (matching how a user expects a cursor drop).
    """
    loc = [float(location[0]), float(location[1]), float(location[2])]
    z = [0.0, 0.0, 1.0]
    d = {k: float(v) for k, v in dims.items()}
    if kind == "BOX":
        return {"center": loc, "ax": [1.0, 0.0, 0.0], "ay": [0.0, 1.0, 0.0],
                "az": z, "hx": d["hx"], "hy": d["hy"], "hz": d["hz"]}
    if kind == "CYLINDER":
        # build._cylinder generates from -h/2..h/2 around its frame origin, so
        # the frame origin (``base``) *is* the body centre.
        return {"base": loc, "axis": z, "radius": d["radius"], "height": d["height"]}
    if kind == "CONE":
        h = d["height"]
        base = [loc[0], loc[1], loc[2] - h / 2.0]   # _cone generates 0..h from base
        half_angle = math.atan(abs(d["radius2"] - d["radius1"]) / h) if h > 1e-12 else 0.0
        return {"base": base, "axis": z, "radius1": d["radius1"],
                "radius2": d["radius2"], "height": h, "half_angle": half_angle}
    if kind == "SPHERE":
        return {"center": loc, "radius": d["radius"]}
    if kind == "TORUS":
        return {"center": loc, "axis": z, "major_radius": d["major_radius"],
                "minor_radius": d["minor_radius"]}
    if kind == "EXTRUDE":
        try:
            from .fitting import profile as profile2d
        except ImportError:                  # standalone (pure-Python tests)
            from fitting import profile as profile2d
        h = d["height"]
        sides = int(d.get("sides", 6))
        base = [loc[0], loc[1], loc[2] - h / 2.0]   # centre the body on the drop
        prof = profile2d.ngon_profile(sides, d["radius"])
        return {"base": base, "axis": z, "xdir": [1.0, 0.0, 0.0], "height": h,
                "radius": d["radius"], "sides": float(sides),
                "profile": [[float(x) for x in row] for row in prof]}
    raise ValueError(f"Not a forward-build kind: {kind}")


def refresh_extrude_profile(params: dict) -> bool:
    """Regenerate an N-gon EXTRUDE's profile after a radius edit (in place).

    Only applies to forward-built prisms (they carry ``sides``); fitted
    extrusions keep their measured profile. Returns True when refreshed.
    """
    if params.get("kind", "EXTRUDE") != "EXTRUDE" or "sides" not in params.keys():
        return False
    try:
        from .fitting import profile as profile2d
    except ImportError:
        from fitting import profile as profile2d
    prof = profile2d.ngon_profile(int(params["sides"]), float(params["radius"]))
    params["profile"] = [[float(x) for x in row] for row in prof]
    return True


def make_result(kind: str, params: dict) -> FitResult:
    """An exact (rms=0) :class:`FitResult` so build.build_object can consume it."""
    return FitResult(kind=kind, rms=0.0, max_error=0.0, params=dict(params),
                     summary=summarize(kind, params))


# --- Blender-dependent helpers (rebuild / drift) --------------------------------


def _current_dims(data, kind: str) -> dict:
    """Pull the editable dimension values for ``kind`` out of an obj["reverse"]."""
    return {key: float(data[key]) for key, _label in PARAM_FIELDS[kind]
            if key in data.keys()}


def rebuild_object(obj, segments: int = 48):
    """Regenerate ``obj``'s mesh from its stored ``["reverse"]`` parameters.

    Geometry is rebuilt in the canonical frame and the object keeps its current
    ``matrix_world``, so a primitive the user moved/rotated stays put — only its
    *shape* snaps back to the stored parameters. The fingerprint is refreshed
    and the stored ``_xform`` re-anchored to the current placement so the
    export's delta machinery sees a clean slate.
    """
    from . import build

    data = obj.get("reverse")
    if data is None:
        raise ValueError(f"{obj.name} has no reverse parameters")
    kind = data["kind"]

    # Regenerate in a frame derived from the stored params, then keep the
    # object's own matrix_world: express the params in the object's local frame
    # by rebuilding them around the current placement.
    params = {k: (list(v) if hasattr(v, "__len__") and not isinstance(v, str) else v)
              for k, v in data.items()}
    # An N-gon prism's profile is derived from radius/sides — regenerate it so
    # a radius edit (or baked scale) is reflected in the rebuilt mesh.
    refreshed = refresh_extrude_profile(params)
    verts, faces, matrix = build.generate_mesh(kind, params, segments)

    mesh = obj.data
    mesh.clear_geometry()
    mesh.from_pydata([tuple(v) for v in verts], [], faces)
    mesh.update()

    # The generated matrix places canonical geometry at the *stored* params'
    # world pose. Honour any move the user made since creation by carrying the
    # existing _xform→matrix_world delta over to the regenerated pose.
    from mathutils import Matrix

    xform = data.get("_xform")
    if xform is not None and len(xform) == 16:
        x = list(xform)
        creation = Matrix([x[0:4], x[4:8], x[8:12], x[12:16]])
        try:
            delta = obj.matrix_world @ creation.inverted()
        except ValueError:
            delta = Matrix.Identity(4)
    else:
        delta = Matrix.Identity(4)
    obj.matrix_world = delta @ matrix

    new = dict(data)
    if refreshed:
        new["profile"] = params["profile"]
    new["_xform"] = [matrix[i][j] for i in range(4) for j in range(4)]
    new["_fingerprint"] = build.mesh_fingerprint(mesh)
    # Reassign the whole dict: nested IDProperty writes don't reliably tag updates.
    obj["reverse"] = new
    return obj


def drift_status(obj):
    """Return a warning string if ``obj`` no longer matches its stored params.

    Checks (a) mesh edits — fingerprint mismatch vs the stored one, and
    (b) non-uniform scale on curved kinds, which has no analytic STEP
    equivalent. Returns ``None`` when the object is clean. Objects without a
    stored fingerprint (built before this feature) only get the scale check.
    """
    from . import build

    data = obj.get("reverse")
    if data is None:
        return None
    kind = data["kind"]
    if kind == "MESH_PATCH":
        return None

    stored = data.get("_fingerprint")
    if stored and obj.mode != "EDIT" and build.mesh_fingerprint(obj.data) != stored:
        return "Mesh edited since build — export uses the stored parameters"

    if kind != "BOX":
        s = obj.matrix_world.to_scale()
        comps = sorted(abs(c) for c in s)
        if comps[0] > 1e-12 and comps[2] / comps[0] > 1.001:
            return ("Non-uniform scale on a curved primitive — not representable "
                    "in STEP; apply or remove the scale")
    return None
