# SPDX-License-Identifier: GPL-3.0-or-later
"""Optional OCCT-backed STEP export.

This path is used only when an OpenCASCADE Python binding is importable
(``OCP`` from cadquery-ocp, or ``OCC`` from pythonocc-core). It is never bundled;
:func:`is_available` lets the UI offer a one-click install and otherwise fall
back to the pure-Python writer in :mod:`reverse_mesh.step_export`.

Its unique value over the pure-Python path is a real geometry kernel: it can
**sew/fuse** the separate fitted primitives into one watertight solid and write a
kernel-grade AP242 file. Same feature schema as :func:`step_export.build_step`.
"""

from __future__ import annotations

import importlib

from .step_export import _add, _cross, _perp, _scale, _sub, _unit  # geometry helpers


def is_available() -> bool:
    """True if an OpenCASCADE binding (OCP or pythonocc) can be imported."""
    for root in ("OCP", "OCC.Core"):
        try:
            importlib.import_module(f"{root}.gp")
            return True
        except Exception:
            continue
    return False


def backend_name() -> str:
    for root in ("OCP", "OCC.Core"):
        try:
            importlib.import_module(f"{root}.gp")
            return "cadquery-ocp" if root == "OCP" else "pythonocc-core"
        except Exception:
            continue
    return ""


def _imp(module, *names):
    """Import ``names`` from OCP.<module> or OCC.Core.<module>, whichever exists."""
    last = None
    for root in ("OCP", "OCC.Core"):
        try:
            mod = importlib.import_module(f"{root}.{module}")
            return tuple(getattr(mod, n) for n in names)
        except Exception as exc:  # pragma: no cover - depends on which binding
            last = exc
    raise ImportError(f"Cannot import {names} from {module}: {last}")


def _grow_cutter(kind, p, frac):
    """Extend a subtractive cutter along its axis so its ends overshoot the body.

    When a cutter's end cap is coplanar with a face of the base solid, the boolean
    has coincident faces and may not open the hole cleanly. Lengthening the cutter
    by ``frac`` of its own extent at each end removes only "air" beyond the body,
    guaranteeing a clean through-cut. ``frac`` is a fraction (0.05 = 5% each end).
    """
    if frac <= 0:
        return p
    q = dict(p)
    if kind == "CYLINDER":
        # 'base' is the axial midpoint, so growing the height extends both ends.
        q["height"] = p["height"] * (1.0 + 2.0 * frac)
    elif kind == "CONE":
        h = p["height"]
        g = frac * h
        slope = (p["radius2"] - p["radius1"]) / h if h else 0.0
        axis = tuple(float(c) for c in p["axis"])
        base = tuple(float(c) for c in p["base"])
        q["base"] = _sub(base, _scale(axis, g))      # push the r1 end outward
        q["height"] = h + 2.0 * g
        q["radius1"] = max(0.0, p["radius1"] - slope * g)
        q["radius2"] = max(0.0, p["radius2"] + slope * g)
    # Boxes / spheres / tori have no single pair of "ends"; left unchanged.
    return q


def export(features, filepath, *, unit="MM", merge=False, overshoot=0.05):
    """Build OCCT solids from ``features`` and write an AP242 STEP file.

    Returns a short status string. If ``merge`` is set, all solids are fused into
    a single body before writing (planes are added alongside as faces). Subtractive
    cylinders/cones are extended by ``overshoot`` (fraction per end) so their ends
    cut cleanly through coplanar faces.
    """
    (gp_Pnt, gp_Dir, gp_Ax2, gp_Ax3, gp_Pln) = _imp(
        "gp", "gp_Pnt", "gp_Dir", "gp_Ax2", "gp_Ax3", "gp_Pln")
    (MakeBox,) = _imp("BRepPrimAPI", "BRepPrimAPI_MakeBox")
    (MakeCyl,) = _imp("BRepPrimAPI", "BRepPrimAPI_MakeCylinder")
    (MakeCone,) = _imp("BRepPrimAPI", "BRepPrimAPI_MakeCone")
    (MakeSphere,) = _imp("BRepPrimAPI", "BRepPrimAPI_MakeSphere")
    (MakeTorus,) = _imp("BRepPrimAPI", "BRepPrimAPI_MakeTorus")
    (MakeFace,) = _imp("BRepBuilderAPI", "BRepBuilderAPI_MakeFace")
    (Fuse,) = _imp("BRepAlgoAPI", "BRepAlgoAPI_Fuse")
    (Cut,) = _imp("BRepAlgoAPI", "BRepAlgoAPI_Cut")
    (TopoDS_Compound,) = _imp("TopoDS", "TopoDS_Compound")
    (BRep_Builder,) = _imp("BRep", "BRep_Builder")
    (STEPControl_Writer,) = _imp("STEPControl", "STEPControl_Writer")
    (Interface_Static,) = _imp("Interface", "Interface_Static")
    (IFSelect_RetDone,) = _imp("IFSelect", "IFSelect_RetDone")

    def ax2(origin, zdir, xdir):
        return gp_Ax2(gp_Pnt(*[float(c) for c in origin]),
                      gp_Dir(*[float(c) for c in zdir]),
                      gp_Dir(*[float(c) for c in xdir]))

    adds, subs, faces = [], [], []
    for feat in features:
        kind = feat["kind"]
        p = feat["params"]
        op = feat.get("op", "ADD")
        if op == "SUBTRACT":
            p = _grow_cutter(kind, p, overshoot)   # overshoot so ends cut clean
        try:
            shape, is_solid = _make_shape(
                kind, p, gp_Pnt, gp_Dir, gp_Ax3, gp_Pln,
                MakeBox, MakeCyl, MakeCone, MakeSphere, MakeTorus, MakeFace, ax2)
        except Exception:  # skip a bad feature rather than abort the file
            shape = None
            is_solid = False
        if shape is None:
            continue
        if not is_solid:
            faces.append(shape)            # planes can only add
        elif op == "SUBTRACT":
            subs.append(shape)
        else:
            adds.append(shape)

    if not adds and not subs and not faces:
        raise ValueError("No exportable shapes")

    # Boolean assembly whenever something is marked SUBTRACT, or merge is on.
    do_bool = merge or bool(subs)
    parts = list(faces)
    note = ""
    if do_bool and adds:
        base = adds[0]
        for s in adds[1:]:
            base = Fuse(base, s).Shape()
        for s in subs:
            base = Cut(base, s).Shape()    # carve each cutter out of the body
        parts.append(base)
        note = f"1 body (+{len(adds)} / -{len(subs)})"
    else:
        if subs and not adds:
            note = f"{len(subs)} cutter(s) with no base to subtract from"
        parts.extend(adds + subs)
        note = note or f"{len(adds) + len(subs)} solid(s)"

    # Combine into one compound to transfer in a single shape.
    builder = BRep_Builder()
    compound = TopoDS_Compound()
    builder.MakeCompound(compound)
    for part in parts:
        builder.Add(compound, part)

    # The writer's constructor registers the STEP static parameters, so schema/
    # unit must be set *after* it is created. OCCT's AP242 value is "AP242DIS".
    writer = STEPControl_Writer()
    _set_static(Interface_Static, "write.step.schema", "AP242DIS")
    _set_static(Interface_Static, "write.step.unit",
                {"MM": "MM", "M": "M", "IN": "INCH"}.get(unit, "MM"))
    as_is = _as_is()
    writer.Transfer(compound, as_is)
    if writer.Write(filepath) != IFSelect_RetDone:
        raise RuntimeError("STEPControl_Writer.Write failed")

    return f"{note} + {len(faces)} face(s) via {backend_name()}"


def _make_shape(kind, p, gp_Pnt, gp_Dir, gp_Ax3, gp_Pln,
                MakeBox, MakeCyl, MakeCone, MakeSphere, MakeTorus, MakeFace, ax2):
    if kind == "BOX":
        ax = _unit(tuple(p["ax"])); ay = _unit(tuple(p["ay"])); az = _unit(tuple(p["az"]))
        hx, hy, hz = p["hx"], p["hy"], p["hz"]
        corner = tuple(p["center"])
        corner = _add(corner, _scale(ax, -hx))
        corner = _add(corner, _scale(ay, -hy))
        corner = _add(corner, _scale(az, -hz))
        return MakeBox(ax2(corner, az, ax), 2 * hx, 2 * hy, 2 * hz).Shape(), True
    if kind == "CYLINDER":
        axis = _unit(tuple(p["axis"]))
        h = p["height"]
        base = _sub(tuple(p["base"]), _scale(axis, h / 2.0))
        return MakeCyl(ax2(base, axis, _perp(axis)), p["radius"], h).Shape(), True
    if kind == "CONE":
        axis = _unit(tuple(p["axis"]))
        return MakeCone(ax2(tuple(p["base"]), axis, _perp(axis)),
                        p["radius1"], p["radius2"], p["height"]).Shape(), True
    if kind == "SPHERE":
        return MakeSphere(gp_Pnt(*[float(c) for c in p["center"]]), p["radius"]).Shape(), True
    if kind == "TORUS":
        axis = _unit(tuple(p["axis"]))
        return MakeTorus(ax2(tuple(p["center"]), axis, _perp(axis)),
                         p["major_radius"], p["minor_radius"]).Shape(), True
    if kind == "PLANE":
        c = tuple(float(x) for x in p["point"])
        n = tuple(float(x) for x in p["normal"])
        e1 = tuple(float(x) for x in p["e1"])
        pln = gp_Pln(gp_Ax3(gp_Pnt(*c), gp_Dir(*n), gp_Dir(*e1)))
        face = MakeFace(pln, -p["half_u"], p["half_u"], -p["half_v"], p["half_v"]).Face()
        return face, False
    return None, False


def _set_static(Interface_Static, key, value):
    fn = getattr(Interface_Static, "SetCVal_s", None) or getattr(Interface_Static, "SetCVal", None)
    if fn is not None:
        fn(key, value)


def _as_is():
    (sct,) = _imp("STEPControl", "STEPControl_StepModelType")
    return sct.STEPControl_AsIs
