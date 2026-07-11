# SPDX-License-Identifier: GPL-3.0-or-later
"""Validate the AP242 STEP writer without Blender.

Checks structural integrity (every #ref resolves, header/schema/footer present,
expected entity kinds emitted) for one of every primitive. If an OCCT binding
(pythonocc-core / OCC) is importable it additionally re-reads the file and
counts solids — otherwise that step is skipped with a note.

    python3 tests/test_step.py
"""

import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pmi_export  # noqa: E402
import step_export as se  # noqa: E402
import units  # noqa: E402


def _features():
    return [
        {"kind": "PLANE", "name": "p", "params": {
            "point": (0, 0, 0), "normal": (0, 0, 1), "e1": (1, 0, 0), "e2": (0, 1, 0),
            "half_u": 5.0, "half_v": 3.0}},
        {"kind": "BOX", "name": "b", "params": {
            "center": (5, 5, 5), "ax": (1, 0, 0), "ay": (0, 1, 0), "az": (0, 0, 1),
            "hx": 2.0, "hy": 3.0, "hz": 4.0}},
        {"kind": "CYLINDER", "name": "c", "params": {
            "base": (10, 0, 0), "axis": (0, 0, 1), "radius": 2.0, "height": 6.0,
            "thread_spec": "M8x1.25"}},
        {"kind": "CONE", "name": "cn", "params": {
            "base": (20, 0, 0), "axis": (0, 0, 1), "radius1": 3.0, "radius2": 1.0,
            "height": 5.0}},
        {"kind": "CONE", "name": "cn2", "params": {  # pointed cone
            "base": (30, 0, 0), "axis": (0, 0, 1), "radius1": 3.0, "radius2": 0.0,
            "height": 5.0}},
        {"kind": "SPHERE", "name": "s", "params": {"center": (40, 0, 0), "radius": 2.5}},
        {"kind": "TORUS", "name": "t", "params": {
            "center": (50, 0, 0), "axis": (0, 0, 1), "major_radius": 5.0,
            "minor_radius": 1.5}},
        {"kind": "FILLET", "name": "fl", "params": {     # 90° edge fillet patch
            "base": (60, 0, 0), "axis": (0, 0, 1), "ref": (1, 0, 0),
            "radius": 1.0, "height": 4.0, "u_min": 0.0, "u_max": math.pi / 2}},
        {"kind": "EXTRUDE", "name": "ex", "params": {    # stadium/slot prism
            "base": (70, 0, 0), "axis": (0, 0, 1), "xdir": (1, 0, 0), "height": 1.5,
            "profile": [
                [0, -2, -1, 2, -1, 0, 0, 0],
                [1, 2, -1, 2, 1, 2, 0, 1],
                [0, 2, 1, -2, 1, 0, 0, 0],
                [1, -2, 1, -2, -1, -2, 0, 1],
            ]}},
    ]


def main():
    ok = True
    text = se.build_step(_features(), unit="MM", product_name="Test",
                         author="tester", timestamp="2026-05-29T00:00:00")

    def check(name, cond, detail=""):
        nonlocal ok
        ok = ok and cond
        print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")

    check("header", text.startswith("ISO-10303-21;"))
    check("footer", text.rstrip().endswith("END-ISO-10303-21;"))
    check("ap242 schema", se.AP242_SCHEMA in text)
    check("has DATA/ENDSEC", "DATA;" in text and "ENDSEC;" in text)
    check("thread annotation", "M8x1.25" in text and "thread M8x1.25" in text)

    # Every referenced #id must be defined exactly once.
    defined = set(re.findall(r"^#(\d+)=", text, re.MULTILINE))
    data = text.split("DATA;", 1)[1]
    referenced = set(re.findall(r"#(\d+)", data))
    dangling = referenced - defined
    check("no dangling refs", not dangling, f"missing {sorted(dangling)[:5]}")

    # Definition uniqueness.
    all_defs = re.findall(r"^#(\d+)=", text, re.MULTILINE)
    check("unique ids", len(all_defs) == len(set(all_defs)))

    # Expected analytic surface kinds present.
    for ent in ("PLANE(", "CYLINDRICAL_SURFACE(", "CONICAL_SURFACE(",
                "SPHERICAL_SURFACE(", "TOROIDAL_SURFACE(", "MANIFOLD_SOLID_BREP(",
                "CLOSED_SHELL(", "ADVANCED_FACE(", "COLOUR_RGB(",
                "SHAPE_DEFINITION_REPRESENTATION("):
        check(f"emits {ent[:-1]}", ent in text)

    n_solids = text.count("MANIFOLD_SOLID_BREP(")
    check("solid count", n_solids == 7,   # box+cyl+2cone+sph+torus+extrude
          f"got {n_solids}")              # plane/fillet are surface models

    # All reals carry a decimal point (spot-check there are no bare integers in coords).
    bad = re.findall(r"CARTESIAN_POINT\('',\(([^)]*)\)", text)
    bad_nums = [seg for seg in bad if re.search(r"(?<![.\dEe])-?\d+(?![.\dEe])", seg)]
    check("reals have decimal point", not bad_nums, f"{bad_nums[:2]}")

    # Save for manual inspection / optional kernel import.
    out = os.path.join(os.path.dirname(__file__), "sample_export.step")
    with open(out, "w") as f:
        f.write(text)
    print(f"[info] wrote {out} ({len(text)} bytes, {len(all_defs)} entities)")

    # Optional: round-trip through OCCT if available.
    try:
        from OCC.Core.STEPControl import STEPControl_Reader  # type: ignore
        from OCC.Core.IFSelect import IFSelect_RetDone  # type: ignore
        reader = STEPControl_Reader()
        status = reader.ReadFile(out)
        if status == IFSelect_RetDone:
            reader.TransferRoots()
            shape = reader.OneShape()
            check("OCCT import", not shape.IsNull())
        else:
            check("OCCT import", False, f"status={status}")
    except ImportError:
        print("[skip] OCCT not available — structural checks only")

    # PMI sidecar (#11a): dimensions + relationships from the same feature dicts.
    pmi = pmi_export.build_pmi(_features())
    cyl = next(f for f in pmi["features"] if f["kind"] == "CYLINDER")
    check("pmi cylinder diameter", abs(cyl["dimensions"]["diameter"] - 4.0) < 1e-9,
          f"d={cyl['dimensions'].get('diameter')}")
    check("pmi carries thread", cyl["dimensions"].get("thread") == "M8x1.25")
    sphere = next(f for f in pmi["features"] if f["kind"] == "SPHERE")
    check("pmi sphere radius", abs(sphere["dimensions"]["radius"] - 2.5) < 1e-9)
    # cylinder@(10,0,0) ↔ sphere@(40,0,0) → distance 30 along x.
    dists = [r["value"] for r in pmi["relationships"] if r["type"] == "distance"]
    check("pmi has hole spacing", any(abs(d - 30.0) < 1e-6 for d in dists),
          f"distances include 30? {any(abs(d-30.0)<1e-6 for d in dists)}")
    check("pmi has axis angles",
          any(r["type"] == "axis_angle_deg" for r in pmi["relationships"]))

    # Semantic AP242 PMI (#11b): DIMENSIONAL_SIZE entities, refs still resolve.
    ptext = se.build_step(_features(), unit="MM", product_name="PMI", pmi=True)
    pdefs = set(re.findall(r"^#(\d+)=", ptext, re.MULTILINE))
    prefs = set(re.findall(r"#(\d+)", ptext.split("DATA;", 1)[1]))
    check("semantic pmi entities", "DIMENSIONAL_SIZE(" in ptext
          and "SHAPE_DIMENSION_REPRESENTATION(" in ptext
          and "MEASURE_REPRESENTATION_ITEM(" in ptext)
    check("semantic pmi no dangling refs", not (prefs - pdefs),
          f"missing {sorted(prefs - pdefs)[:5]}")
    n_dims = ptext.count("DIMENSIONAL_SIZE(")
    check("semantic pmi dimension count", n_dims >= 5, f"got {n_dims}")  # cyl/cone/sph/torus/fillet

    # Cutter handling: the writer has no boolean kernel, so SUBTRACT features
    # are skipped / marked / written as-is per cutter_mode.
    cut_feats = [
        {"kind": "BOX", "name": "body", "params": {
            "center": (0, 0, 0), "ax": (1, 0, 0), "ay": (0, 1, 0), "az": (0, 0, 1),
            "hx": 2.0, "hy": 2.0, "hz": 2.0}, "op": "ADD"},
        {"kind": "CYLINDER", "name": "hole", "params": {
            "base": (0, 0, 0), "axis": (0, 0, 1), "radius": 0.5, "height": 5.0},
         "op": "SUBTRACT"},
    ]
    legacy = se.build_step(cut_feats, unit="MM")
    default = se.build_step(cut_feats, unit="MM", cutter_mode="SOLID")
    check("cutter default is legacy SOLID", legacy == default)
    check("cutter SOLID writes both solids",
          legacy.count("MANIFOLD_SOLID_BREP(") == 2)

    skipped = se.build_step(cut_feats, unit="MM", cutter_mode="SKIP")
    check("cutter SKIP drops the cutter",
          skipped.count("MANIFOLD_SOLID_BREP(") == 1
          and "CYLINDRICAL_SURFACE(" not in skipped)
    sdefs = set(re.findall(r"^#(\d+)=", skipped, re.MULTILINE))
    srefs = set(re.findall(r"#(\d+)", skipped.split("DATA;", 1)[1]))
    check("cutter SKIP no dangling refs", not (srefs - sdefs))

    marked = se.build_step(cut_feats, unit="MM", cutter_mode="MARK")
    check("cutter MARK keeps both solids",
          marked.count("MANIFOLD_SOLID_BREP(") == 2)
    check("cutter MARK names the cutter", "'cutter:cylinder'" in marked)
    r, g, b = se.CUTTER_COLOR
    check("cutter MARK forces red colour",
          f"COLOUR_RGB('',{se._num(r)},{se._num(g)},{se._num(b)})" in marked)
    check("cutter MARK leaves the body name alone", "'cutter:box'" not in marked)

    # Leftover mesh patch: triangles → faceted planar faces in a surface model;
    # degenerate triangles are skipped.
    patch = [{"kind": "MESH_PATCH", "name": "lo", "params": {
        "verts": [(0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0), (2, 2, 2)],
        "tris": [(0, 1, 2), (1, 3, 2), (4, 4, 0)],   # last is degenerate
    }}]
    mtext = se.build_step(patch, unit="MM")
    check("mesh patch face count", mtext.count("ADVANCED_FACE(") == 2,
          f"got {mtext.count('ADVANCED_FACE(')}")
    check("mesh patch is a surface model",
          "SHELL_BASED_SURFACE_MODEL(" in mtext
          and "MANIFOLD_SOLID_BREP(" not in mtext)
    mdefs = set(re.findall(r"^#(\d+)=", mtext, re.MULTILINE))
    mrefs = set(re.findall(r"#(\d+)", mtext.split("DATA;", 1)[1]))
    check("mesh patch no dangling refs", not (mrefs - mdefs))
    # An all-degenerate patch must not emit an empty shell (or crash).
    empty = se.build_step(
        [{"kind": "MESH_PATCH", "name": "x",
          "params": {"verts": [(0, 0, 0)], "tris": [(0, 0, 0)]}}], unit="MM")
    check("all-degenerate patch yields no shell",
          "SHELL_BASED_SURFACE_MODEL(" not in empty)

    # Unit-aware export scale (scene units → STEP units).
    check("units: metric default scene → mm",
          abs(units.effective_scale("MM", system="METRIC", scale_length=1.0) - 1000.0) < 1e-9)
    check("units: mm-scale scene → mm passthrough",
          abs(units.effective_scale("MM", system="METRIC", scale_length=0.001) - 1.0) < 1e-9)
    check("units: metric scene → meters",
          abs(units.effective_scale("M", system="METRIC", scale_length=1.0) - 1.0) < 1e-9)
    check("units: inch-scale scene → inches",
          abs(units.effective_scale("IN", system="IMPERIAL", scale_length=0.0254) - 1.0) < 1e-9)
    check("units: unitless scene passes through",
          units.effective_scale("MM", system="NONE", scale_length=123.0) == 1.0)
    check("units: imperial scene defaults to IN",
          units.step_unit_for_scene("IMPERIAL", "INCHES") == "IN")
    check("units: meters display defaults to M",
          units.step_unit_for_scene("METRIC", "METERS") == "M")
    check("units: millimeter display defaults to MM",
          units.step_unit_for_scene("METRIC", "MILLIMETERS") == "MM")
    check("units: unitless scene defaults to MM",
          units.step_unit_for_scene("NONE", "") == "MM")

    print(f"\n{'ALL STEP CHECKS PASSED' if ok else 'STEP CHECKS FAILED'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
