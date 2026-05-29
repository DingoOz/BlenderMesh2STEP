# SPDX-License-Identifier: GPL-3.0-or-later
"""Optional OCCT export test. Skips cleanly when no OpenCASCADE binding is present.

Run inside Blender (so bpy is importable for the package), with the binding on
sys.path:
    blender --background --python reverse_mesh/tests/test_occ_export.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from reverse_mesh import occ_export  # noqa: E402


def fail(m):
    print("[FAIL]", m)
    sys.exit(1)


def main():
    if not occ_export.is_available():
        print("[skip] no OpenCASCADE binding (OCP/pythonocc) — OCCT export not tested")
        return

    feats = [
        {"kind": "BOX", "name": "b", "params": {
            "center": (0, 0, 0), "ax": (1, 0, 0), "ay": (0, 1, 0), "az": (0, 0, 1),
            "hx": 2.0, "hy": 2.0, "hz": 2.0}},
        {"kind": "CYLINDER", "name": "c", "params": {
            "base": (0.5, 0, 0), "axis": (0, 0, 1), "radius": 1.0, "height": 8.0}},
        {"kind": "SPHERE", "name": "s", "params": {"center": (20, 0, 0), "radius": 2.5}},
    ]
    out = os.path.join(os.path.dirname(__file__), "occ_sample.step")
    info = occ_export.export(feats, out, unit="MM", merge=False)
    print("[info]", info)

    head = open(out).read(2000)
    if "10303 442" not in head:                 # AP242 schema identifier
        fail("not AP242")

    from OCP.STEPControl import STEPControl_Reader
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.BRepCheck import BRepCheck_Analyzer

    r = STEPControl_Reader()
    if r.ReadFile(out) != IFSelect_RetDone:
        fail("re-read failed")
    r.TransferRoots()
    sh = r.OneShape()
    exp = TopExp_Explorer(sh, TopAbs_SOLID)
    n = 0
    while exp.More():
        n += 1
        exp.Next()
    if sh.IsNull() or not BRepCheck_Analyzer(sh).IsValid() or n != 3:
        fail(f"invalid OCCT output (solids={n})")
    print(f"[ok] OCCT AP242 export: {n} valid solids, round-tripped")

    # Merge overlapping box+cylinder.
    out2 = os.path.join(os.path.dirname(__file__), "occ_merge.step")
    occ_export.export(feats, out2, unit="MM", merge=True)
    r2 = STEPControl_Reader()
    r2.ReadFile(out2)
    r2.TransferRoots()
    sh2 = r2.OneShape()
    exp = TopExp_Explorer(sh2, TopAbs_SOLID)
    n2 = 0
    while exp.More():
        n2 += 1
        exp.Next()
    if n2 >= 3:
        fail(f"merge did not fuse (solids={n2})")
    print(f"[ok] OCCT merge fused overlapping solids → {n2} (from 3)")

    # Boolean SUBTRACT: a box with a cylinder drilled through it.
    from OCP.GProp import GProp_GProps
    from OCP.BRepGProp import BRepGProp
    sub_feats = [
        {"kind": "BOX", "op": "ADD", "name": "base", "params": {
            "center": (0, 0, 0), "ax": (1, 0, 0), "ay": (0, 1, 0), "az": (0, 0, 1),
            "hx": 2.0, "hy": 2.0, "hz": 2.0}},                       # 4×4×4 = 64
        {"kind": "CYLINDER", "op": "SUBTRACT", "name": "hole", "params": {
            "base": (0, 0, 0), "axis": (0, 0, 1), "radius": 1.0, "height": 10.0}},
    ]
    out3 = os.path.join(os.path.dirname(__file__), "occ_cut.step")
    occ_export.export(sub_feats, out3, unit="MM", merge=False)
    r3 = STEPControl_Reader()
    r3.ReadFile(out3)
    r3.TransferRoots()
    sh3 = r3.OneShape()
    exp = TopExp_Explorer(sh3, TopAbs_SOLID)
    n3 = 0
    while exp.More():
        n3 += 1
        exp.Next()
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(sh3, props)
    vol = props.Mass()
    import math
    expected = 64.0 - math.pi * 1.0 ** 2 * 4.0      # box minus the drilled cylinder
    ok = (n3 == 1 and BRepCheck_Analyzer(sh3).IsValid() and abs(vol - expected) < 0.5)
    print(f"[info] cut: solids={n3} volume={vol:.3f} expected={expected:.3f}")
    if not ok:
        fail("boolean subtract produced wrong/invalid result")
    print("[ok] OCCT boolean SUBTRACT: box with drilled hole, volume correct")
    try:
        os.remove(out3)
    except OSError:
        pass

    for f in (out, out2):
        try:
            os.remove(f)
        except OSError:
            pass
    print("\nOCCT EXPORT TEST PASSED")


if __name__ == "__main__":
    main()
