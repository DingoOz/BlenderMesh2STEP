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
import math

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


class ExportReport(str):
    """The export status string, carrying structured validation data.

    Subclasses ``str`` so every existing caller that logs or substring-tests the
    status keeps working, while :mod:`reverse_mesh.operators` can read the
    per-solid ``volumes``/validity to fill the validation report panel.
    """

    def __new__(cls, summary, *, solids=(), free_edges=None,
                watertight=None, valid=None, backend=""):
        obj = super().__new__(cls, summary)
        obj.solids = list(solids)          # [{"index", "volume", "valid"}]
        obj.free_edges = free_edges        # int, or None if no watertight pass
        obj.watertight = watertight        # True/False/None
        obj.valid = valid                  # whole-shape BRepCheck validity
        obj.backend = backend
        return obj


def _solid_report(shape):
    """Per-solid (volume, validity) for every TopAbs_SOLID in ``shape``."""
    (TopExp_Explorer,) = _imp("TopExp", "TopExp_Explorer")
    (TopAbs_SOLID,) = _imp("TopAbs", "TopAbs_SOLID")
    (GProp_GProps,) = _imp("GProp", "GProp_GProps")
    (BRepGProp,) = _imp("BRepGProp", "BRepGProp")
    (BRepCheck_Analyzer,) = _imp("BRepCheck", "BRepCheck_Analyzer")
    out = []
    exp = TopExp_Explorer(shape, TopAbs_SOLID)
    i = 0
    while exp.More():
        s = exp.Current()
        props = GProp_GProps()
        BRepGProp.VolumeProperties_s(s, props)
        try:
            valid = bool(BRepCheck_Analyzer(s).IsValid())
        except Exception:
            valid = False
        out.append({"index": i, "volume": float(props.Mass()), "valid": valid})
        i += 1
        exp.Next()
    return out


def _grow_cutter(kind, p, frac, ends="BOTH"):
    """Extend a subtractive cutter along its axis so its end(s) overshoot the body.

    When a cutter's end cap is coplanar with a face of the base solid, the boolean
    has coincident faces and may not open the hole cleanly. Extending past the
    surface removes only "air" beyond the body, guaranteeing a clean cut. ``ends``
    selects which axial extreme to grow: ``"BOTH"`` (through-hole), ``"LOW"`` /
    ``"HIGH"`` (one open end of a blind pocket), or ``"NONE"``. ``frac`` is the
    fraction of the cutter's own length added at each grown end.

    For cylinders, ``"LOW"`` is the −axis end and ``"HIGH"`` the +axis end. For
    cones, ``"LOW"`` is the r1 (base) end and ``"HIGH"`` the r2 end.
    """
    if frac <= 0 or ends == "NONE":
        return p
    grow_low = ends in ("BOTH", "LOW")
    grow_high = ends in ("BOTH", "HIGH")
    q = dict(p)
    axis = _unit(tuple(float(c) for c in p["axis"]))

    if kind == "CYLINDER":
        h = p["height"]
        g = frac * h
        mid = tuple(float(c) for c in p["base"])      # axial midpoint
        add = (g if grow_low else 0.0) + (g if grow_high else 0.0)
        # Shift the midpoint by half the *net* growth so only the chosen ends move.
        shift = (g if grow_high else 0.0) - (g if grow_low else 0.0)
        q["height"] = h + add
        q["base"] = _add(mid, _scale(axis, shift / 2.0))
    elif kind == "CONE":
        h = p["height"]
        g = frac * h
        slope = (p["radius2"] - p["radius1"]) / h if h else 0.0
        base = tuple(float(c) for c in p["base"])      # r1 (low) end
        q["base"] = _sub(base, _scale(axis, g)) if grow_low else base
        q["height"] = h + (g if grow_low else 0.0) + (g if grow_high else 0.0)
        q["radius1"] = max(0.0, p["radius1"] - slope * g) if grow_low else p["radius1"]
        q["radius2"] = max(0.0, p["radius2"] + slope * g) if grow_high else p["radius2"]
    # Boxes / spheres / tori have no single pair of "ends"; left unchanged.
    return q


def _expand_preset(p):
    """Extra coaxial cutter(s) for a counterbore/countersink hole, or [].

    A preset hole is a base through-cylinder (cut normally) plus a wider recess at
    its open (+axis) end: a flat-bottomed cylinder for a counterbore, or a tapered
    cone for a countersink. Returns ``[(kind, params, grow_ends)]`` to subtract in
    addition to the base hole.
    """
    preset = p.get("hole_preset", "NONE")
    if preset not in ("COUNTERBORE", "COUNTERSINK"):
        return []
    axis = _unit(tuple(float(c) for c in p["axis"]))
    h = float(p["height"])
    r = float(p["radius"])
    high_end = _add(tuple(float(c) for c in p["base"]), _scale(axis, h / 2.0))
    cr = float(p.get("cbore_radius", 2.0 * r))
    if cr <= r:
        return []                                    # recess must be wider than the hole

    if preset == "COUNTERBORE":
        cd = float(p.get("cbore_depth", 0.25 * h))
        c_mid = _sub(high_end, _scale(axis, cd / 2.0))
        return [("CYLINDER", {"base": c_mid, "axis": axis, "radius": cr, "height": cd}, "HIGH")]

    # COUNTERSINK: cone wide at the surface, narrowing to the hole radius inward.
    half = math.radians(float(p.get("csink_angle", 90.0))) / 2.0
    depth = (cr - r) / math.tan(half) if math.tan(half) > 1e-9 else 0.25 * h
    return [("CONE", {"base": high_end, "axis": _scale(axis, -1.0),
                      "radius1": cr, "radius2": r, "height": depth}, "LOW")]


def _cutter_end_centers(kind, p):
    """World-space centres of a cutter's two end caps, as (low, high) or None."""
    axis = _unit(tuple(float(c) for c in p["axis"]))
    if kind == "CYLINDER":
        mid = tuple(float(c) for c in p["base"])
        half = _scale(axis, p["height"] / 2.0)
        return _sub(mid, half), _add(mid, half)
    if kind == "CONE":
        base = tuple(float(c) for c in p["base"])
        return base, _add(base, _scale(axis, p["height"]))
    return None


def _unify(shape):
    """Merge coincident faces/edges of a fused shape into shared topology.

    After fusing abutting solids, OCCT leaves their touching faces split along the
    old boundaries (independent edges). ``ShapeUpgrade_UnifySameDomain`` collapses
    coplanar faces and coincident edges so neighbours genuinely share edges —
    turning two fused boxes into one box with 6 faces, not 10.
    """
    (Unify,) = _imp("ShapeUpgrade", "ShapeUpgrade_UnifySameDomain")
    u = Unify(shape, True, True, True)   # unify edges, unify faces, concat b-splines
    u.Build()
    return u.Shape()


def _make_watertight(shape, tol):
    """Sew all faces of ``shape`` into shells, solidify and heal them.

    Returns ``(result_shape, n_solids, n_free_edges)``. ``n_free_edges == 0`` means
    every boundary was matched — the result is closed (watertight). Faces that
    don't meet within ``tol`` leave free edges and are reported, not hidden.
    """
    (Sewing,) = _imp("BRepBuilderAPI", "BRepBuilderAPI_Sewing")
    (MakeSolid,) = _imp("BRepBuilderAPI", "BRepBuilderAPI_MakeSolid")
    (ShapeFix_Shape,) = _imp("ShapeFix", "ShapeFix_Shape")
    (TopExp_Explorer,) = _imp("TopExp", "TopExp_Explorer")
    (TopAbs_FACE, TopAbs_SHELL) = _imp("TopAbs", "TopAbs_FACE", "TopAbs_SHELL")
    (TopoDS,) = _imp("TopoDS", "TopoDS")
    (BRep_Builder,) = _imp("BRep", "BRep_Builder")
    (TopoDS_Compound,) = _imp("TopoDS", "TopoDS_Compound")

    sew = Sewing(tol)
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        sew.Add(exp.Current())
        exp.Next()
    sew.Perform()
    sewn = sew.SewedShape()
    free_edges = sew.NbFreeEdges()

    builder = BRep_Builder()
    comp = TopoDS_Compound()
    builder.MakeCompound(comp)
    n_solids = 0
    found_shell = False
    sh = TopExp_Explorer(sewn, TopAbs_SHELL)
    while sh.More():
        found_shell = True
        try:
            solid = MakeSolid(TopoDS.Shell_s(sh.Current())).Solid()
            fix = ShapeFix_Shape(solid)
            fix.Perform()
            builder.Add(comp, fix.Shape())
            n_solids += 1
        except Exception:
            builder.Add(comp, sh.Current())
        sh.Next()
    if not found_shell:
        return sewn, 0, free_edges
    return comp, n_solids, free_edges


def export(features, filepath, *, unit="MM", merge=False, overshoot=0.05,
           watertight=False, sew_tol=0.01, auto_stitch=False):
    """Build OCCT solids from ``features`` and write an AP242 STEP file.

    Returns a short status string. If ``merge`` is set, all solids are fused into
    a single body before writing (planes are added alongside as faces). Subtractive
    cylinders/cones are extended by ``overshoot`` (fraction per end) so their ends
    cut cleanly through coplanar faces. ``auto_stitch`` fuses the additive solids
    and unifies their coincident faces into shared topology (best-effort).
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

    def build(kind, p):
        try:
            return _make_shape(kind, p, gp_Pnt, gp_Dir, gp_Ax3, gp_Pln,
                               MakeBox, MakeCyl, MakeCone, MakeSphere, MakeTorus,
                               MakeFace, ax2)
        except Exception:
            return None, False

    def blind_open_end(base_solid, kind, p):
        """Which end of a blind cutter is open (outside the base): LOW/HIGH/BOTH."""
        centers = _cutter_end_centers(kind, p)
        if base_solid is None or centers is None:
            return "BOTH"
        try:
            (Classifier,) = _imp("BRepClass3d", "BRepClass3d_SolidClassifier")
            (TopAbs_IN,) = _imp("TopAbs", "TopAbs_IN")

            def inside(pt):
                clf = Classifier(base_solid, gp_Pnt(*[float(c) for c in pt]), 1e-7)
                return clf.State() == TopAbs_IN

            low_in, high_in = inside(centers[0]), inside(centers[1])
        except Exception:
            return "BOTH"
        if low_in and not high_in:
            return "HIGH"
        if high_in and not low_in:
            return "LOW"
        if not low_in and not high_in:
            return "BOTH"      # through-like
        return "NONE"          # fully buried cavity
    (STEPControl_Writer,) = _imp("STEPControl", "STEPControl_Writer")
    (Interface_Static,) = _imp("Interface", "Interface_Static")
    (IFSelect_RetDone,) = _imp("IFSelect", "IFSelect_RetDone")

    def ax2(origin, zdir, xdir):
        return gp_Ax2(gp_Pnt(*[float(c) for c in origin]),
                      gp_Dir(*[float(c) for c in zdir]),
                      gp_Dir(*[float(c) for c in xdir]))

    # Separate the features by role; cutters are built later (they may need the
    # finished base solid to decide which end of a blind hole is open).
    adds, faces, cutters = [], [], []
    for feat in features:
        kind = feat["kind"]
        op = feat.get("op", "ADD")
        if op == "SUBTRACT":
            cutters.append(feat)
            continue
        shape, is_solid = build(kind, feat["params"])
        if shape is None:
            continue
        (adds if is_solid else faces).append(shape)

    if not adds and not cutters and not faces:
        raise ValueError("No exportable shapes")

    do_bool = merge or auto_stitch or bool(cutters)
    parts = list(faces)
    note = ""
    if do_bool and adds:
        base = adds[0]
        for s in adds[1:]:
            base = Fuse(base, s).Shape()
        n_cut = 0
        for feat in cutters:
            kind, p = feat["kind"], feat["params"]
            if feat.get("cut", "THROUGH") == "BLIND":
                ends = blind_open_end(base, kind, p)   # overshoot only the open end
            else:
                ends = "BOTH"                          # through-hole
            cutter, ok = build(kind, _grow_cutter(kind, p, overshoot, ends))
            if cutter is None:
                continue
            base = Cut(base, cutter).Shape()
            n_cut += 1
            # Counterbore / countersink: subtract the extra recess at the open end.
            for sk, sp, sgrow_ends in _expand_preset(p):
                sub_shape, _ = build(sk, _grow_cutter(sk, sp, overshoot, sgrow_ends))
                if sub_shape is not None:
                    base = Cut(base, sub_shape).Shape()
        if auto_stitch:
            try:
                base = _unify(base)
                note_stitch = " — stitched (shared topology)"
            except Exception as exc:
                note_stitch = f" — stitch failed: {exc}"
        else:
            note_stitch = ""
        parts.append(base)
        note = f"1 body (+{len(adds)} / -{n_cut}){note_stitch}"
    else:
        # No base to cut from: export everything separately (overshoot is moot).
        for feat in cutters:
            shape, ok = build(feat["kind"], feat["params"])
            if shape is not None:
                parts.append(shape)
        parts.extend(adds)
        if cutters and not adds:
            note = f"{len(cutters)} cutter(s) with no base to subtract from"
        note = note or f"{len(adds) + len(cutters)} solid(s)"

    # Combine into one compound to transfer in a single shape.
    builder = BRep_Builder()
    compound = TopoDS_Compound()
    builder.MakeCompound(compound)
    for part in parts:
        builder.Add(compound, part)

    free_edges = None
    watertight_ok = None
    if watertight:
        try:
            healed, n_solids, free_edges = _make_watertight(compound, sew_tol)
            compound = healed
            watertight_ok = free_edges == 0
            if free_edges == 0:
                note += f" — watertight ({n_solids} closed solid(s))"
            else:
                note += f" — NOT watertight: {free_edges} free edge(s) remain"
        except Exception as exc:
            note += f" — watertight pass failed: {exc}"

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

    # Validation report: per-solid volume + validity, surfaced to the user.
    try:
        solids = _solid_report(compound)
        (BRepCheck_Analyzer,) = _imp("BRepCheck", "BRepCheck_Analyzer")
        all_valid = bool(BRepCheck_Analyzer(compound).IsValid())
    except Exception:
        solids, all_valid = [], None

    summary = f"{note} + {len(faces)} face(s) via {backend_name()}"
    return ExportReport(summary, solids=solids, free_edges=free_edges,
                        watertight=watertight_ok, valid=all_valid,
                        backend=backend_name())


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
