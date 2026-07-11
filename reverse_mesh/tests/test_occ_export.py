# SPDX-License-Identifier: GPL-3.0-or-later
"""Optional OCCT export test. Skips cleanly when no OpenCASCADE binding is present.

Run inside Blender (so bpy is importable for the package), with the binding on
sys.path:
    blender --background --python reverse_mesh/tests/test_occ_export.py
"""

import math
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

    # Validation report (#10): structured per-solid volume + validity surfaced.
    if not hasattr(info, "solids") or len(info.solids) != 3:
        fail(f"export report missing per-solid data: {getattr(info, 'solids', None)}")
    if not all(s["valid"] for s in info.solids):
        fail(f"report flagged invalid solids: {info.solids}")
    sph = next((s for s in info.solids if abs(s["volume"] - (4.0 / 3.0 * math.pi * 2.5 ** 3)) < 0.5), None)
    if sph is None:
        fail(f"sphere volume not reported correctly: {[s['volume'] for s in info.solids]}")
    if info.valid is not True:
        fail(f"overall validity not True: {info.valid}")
    print(f"[ok] validation report: {len(info.solids)} solids, volumes "
          f"{[round(s['volume'], 2) for s in info.solids]}, all valid")

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

    # Counterbore (#6): a through hole (r1) with a wider flat recess (r1.5, depth1)
    # at the top. Volume = 64 − π·1²·4 (bore) − π·(1.5²−1²)·1 (counterbore ring).
    cbore = [
        {"kind": "BOX", "op": "ADD", "name": "base", "params": {
            "center": (0, 0, 0), "ax": (1, 0, 0), "ay": (0, 1, 0), "az": (0, 0, 1),
            "hx": 2.0, "hy": 2.0, "hz": 2.0}},
        {"kind": "CYLINDER", "op": "SUBTRACT", "name": "hole", "params": {
            "base": (0, 0, 0), "axis": (0, 0, 1), "radius": 1.0, "height": 4.0,
            "hole_preset": "COUNTERBORE", "cbore_radius": 1.5, "cbore_depth": 1.0}},
    ]
    out_cb = os.path.join(os.path.dirname(__file__), "occ_cbore.step")
    occ_export.export(cbore, out_cb, unit="MM", merge=False, overshoot=0.05)
    rcb = STEPControl_Reader(); rcb.ReadFile(out_cb); rcb.TransferRoots(); shcb = rcb.OneShape()
    prcb = GProp_GProps(); BRepGProp.VolumeProperties_s(shcb, prcb); vcb = prcb.Mass()
    expected_cb = 64.0 - math.pi * 4.0 - math.pi * (1.5 ** 2 - 1.0 ** 2) * 1.0
    valid_cb = BRepCheck_Analyzer(shcb).IsValid()
    print(f"[info] counterbore: volume={vcb:.3f} expected={expected_cb:.3f} valid={valid_cb}")
    if not valid_cb or abs(vcb - expected_cb) > 0.5:
        fail(f"counterbore volume wrong (vol={vcb:.3f}, expected {expected_cb:.3f})")
    print("[ok] counterbore: through hole + flat recess, volume correct")
    try:
        os.remove(out_cb)
    except OSError:
        pass

    # Coplanar ends: a cutter whose height exactly spans the box (z -2..2). The
    # overshoot must still open a clean through-hole on the coplanar top/bottom.
    from OCP.TopAbs import TopAbs_FACE
    coplanar = [
        {"kind": "BOX", "op": "ADD", "name": "base", "params": {
            "center": (0, 0, 0), "ax": (1, 0, 0), "ay": (0, 1, 0), "az": (0, 0, 1),
            "hx": 2.0, "hy": 2.0, "hz": 2.0}},
        {"kind": "CYLINDER", "op": "SUBTRACT", "name": "hole", "params": {
            "base": (0, 0, 0), "axis": (0, 0, 1), "radius": 1.0, "height": 4.0}},  # exactly spans
    ]
    out4 = os.path.join(os.path.dirname(__file__), "occ_coplanar.step")
    occ_export.export(coplanar, out4, unit="MM", merge=False, overshoot=0.05)
    r4 = STEPControl_Reader()
    r4.ReadFile(out4)
    r4.TransferRoots()
    sh4 = r4.OneShape()
    n4 = 0
    e4 = TopExp_Explorer(sh4, TopAbs_SOLID)
    while e4.More():
        n4 += 1
        e4.Next()
    nf4 = 0
    e4 = TopExp_Explorer(sh4, TopAbs_FACE)
    while e4.More():
        nf4 += 1
        e4.Next()
    pr = GProp_GProps(); BRepGProp.VolumeProperties_s(sh4, pr); v4 = pr.Mass()
    # A clean through-hole box has 7 faces: 4 sides + holed top + holed bottom + bore.
    ok4 = (n4 == 1 and BRepCheck_Analyzer(sh4).IsValid()
           and abs(v4 - expected) < 0.5 and nf4 == 7)
    print(f"[info] coplanar cut: solids={n4} faces={nf4} volume={v4:.3f} valid={BRepCheck_Analyzer(sh4).IsValid()}")
    if not ok4:
        fail(f"coplanar through-hole not clean (faces={nf4}, vol={v4:.3f})")
    print("[ok] coplanar cutter ends → clean through-hole (7 faces, bore opens both ends)")
    try:
        os.remove(out4)
    except OSError:
        pass

    # Blind pocket: cutter open at the top (z=2), bottom at z=0 (inside the box).
    # BLIND mode must keep the floor depth exact while still opening at the top.
    blind = [
        {"kind": "BOX", "op": "ADD", "name": "base", "params": {
            "center": (0, 0, 0), "ax": (1, 0, 0), "ay": (0, 1, 0), "az": (0, 0, 1),
            "hx": 2.0, "hy": 2.0, "hz": 2.0}},
        {"kind": "CYLINDER", "op": "SUBTRACT", "cut": "BLIND", "name": "pocket",
         "params": {"base": (0, 0, 1.0), "axis": (0, 0, 1), "radius": 1.0,
                    "height": 2.0}},   # spans z=0..2; top (z=2) coplanar, floor at z=0
    ]
    out5 = os.path.join(os.path.dirname(__file__), "occ_blind.step")
    occ_export.export(blind, out5, unit="MM", merge=False, overshoot=0.05)
    r5 = STEPControl_Reader(); r5.ReadFile(out5); r5.TransferRoots(); sh5 = r5.OneShape()
    pr5 = GProp_GProps(); BRepGProp.VolumeProperties_s(sh5, pr5); v5 = pr5.Mass()
    # Pocket removes a depth-2 bore: 64 - π·1²·2 = 64 - 2π = 57.72. If the floor had
    # been overshot through the bottom it would be 64 - 4π = 51.43.
    expected_blind = 64.0 - math.pi * 1.0 ** 2 * 2.0
    valid5 = BRepCheck_Analyzer(sh5).IsValid()
    print(f"[info] blind pocket: volume={v5:.3f} expected={expected_blind:.3f} valid={valid5}")
    if not valid5 or abs(v5 - expected_blind) > 0.5:
        fail(f"blind pocket depth wrong (vol={v5:.3f}, expected {expected_blind:.3f})")
    print("[ok] BLIND pocket: floor depth preserved, top opens cleanly")
    try:
        os.remove(out5)
    except OSError:
        pass

    # Ordered booleans: an ADD plug placed *after* the cut refills part of the
    # hole. Legacy mode fuses the plug into the base first, so the cutter removes
    # it again — the two modes must give different volumes.
    ordered_feats = [
        {"kind": "BOX", "op": "ADD", "name": "base", "params": {
            "center": (0, 0, 0), "ax": (1, 0, 0), "ay": (0, 1, 0), "az": (0, 0, 1),
            "hx": 2.0, "hy": 2.0, "hz": 2.0}},                       # 4×4×4 = 64
        {"kind": "CYLINDER", "op": "SUBTRACT", "name": "hole", "params": {
            "base": (0, 0, 0), "axis": (0, 0, 1), "radius": 1.0, "height": 10.0}},
        {"kind": "BOX", "op": "ADD", "name": "plug", "params": {     # 1×1×4 inside the bore
            "center": (0, 0, 0), "ax": (1, 0, 0), "ay": (0, 1, 0), "az": (0, 0, 1),
            "hx": 0.5, "hy": 0.5, "hz": 2.0}},
    ]

    def _volume(path):
        rd = STEPControl_Reader(); rd.ReadFile(path); rd.TransferRoots()
        pr = GProp_GProps()
        BRepGProp.VolumeProperties_s(rd.OneShape(), pr)
        return pr.Mass()

    out_ord = os.path.join(os.path.dirname(__file__), "occ_ordered.step")
    occ_export.export(ordered_feats, out_ord, unit="MM", ordered=True)
    v_ord = _volume(out_ord)
    expected_ord = 64.0 - math.pi * 4.0 + 4.0       # plug restored after the cut
    occ_export.export(ordered_feats, out_ord, unit="MM", ordered=False)
    v_leg = _volume(out_ord)
    expected_leg = 64.0 - math.pi * 4.0             # plug fused first, then drilled away
    print(f"[info] ordered: vol={v_ord:.3f} (expect {expected_ord:.3f}); "
          f"legacy: vol={v_leg:.3f} (expect {expected_leg:.3f})")
    if abs(v_ord - expected_ord) > 0.5:
        fail(f"ordered booleans wrong volume ({v_ord:.3f} != {expected_ord:.3f})")
    if abs(v_leg - expected_leg) > 0.5:
        fail(f"legacy boolean order regressed ({v_leg:.3f} != {expected_leg:.3f})")
    # An add-then-cut stack is identical in both modes — guard the shared cutter path.
    occ_export.export(sub_feats, out_ord, unit="MM", ordered=True)
    v_same = _volume(out_ord)
    if abs(v_same - expected) > 0.5:
        fail(f"ordered mode changed a plain subtract ({v_same:.3f} != {expected:.3f})")
    print("[ok] ordered booleans: stack order honoured; legacy mode unchanged")
    try:
        os.remove(out_ord)
    except OSError:
        pass

    # Auto-stitch (#4): two boxes abutting at z=0 form a 2x2x4 bar. Fuse + unify
    # must give ONE solid with 6 faces (coplanar sides merged), not 10.
    two_boxes = [
        {"kind": "BOX", "op": "ADD", "name": "low", "params": {
            "center": (0, 0, -1), "ax": (1, 0, 0), "ay": (0, 1, 0), "az": (0, 0, 1),
            "hx": 1.0, "hy": 1.0, "hz": 1.0}},
        {"kind": "BOX", "op": "ADD", "name": "high", "params": {
            "center": (0, 0, 1), "ax": (1, 0, 0), "ay": (0, 1, 0), "az": (0, 0, 1),
            "hx": 1.0, "hy": 1.0, "hz": 1.0}},
    ]
    out_st = os.path.join(os.path.dirname(__file__), "occ_stitch.step")
    info_st = occ_export.export(two_boxes, out_st, unit="MM", auto_stitch=True)
    print("[info] stitch:", info_st)
    rst = STEPControl_Reader(); rst.ReadFile(out_st); rst.TransferRoots(); shst = rst.OneShape()
    n_st = 0
    e = TopExp_Explorer(shst, TopAbs_SOLID)
    while e.More():
        n_st += 1; e.Next()
    nf_st = 0
    e = TopExp_Explorer(shst, TopAbs_FACE)
    while e.More():
        nf_st += 1; e.Next()
    prst = GProp_GProps(); BRepGProp.VolumeProperties_s(shst, prst); v_st = prst.Mass()
    print(f"[info] stitched bar: solids={n_st} faces={nf_st} volume={v_st:.3f} (expect 1, 6, 16)")
    if n_st != 1 or nf_st != 6 or abs(v_st - 16.0) > 0.01 or not BRepCheck_Analyzer(shst).IsValid():
        fail(f"auto-stitch did not unify two boxes (solids={n_st}, faces={nf_st}, vol={v_st:.3f})")
    print("[ok] auto-stitch: two abutting boxes → 1 solid, 6 shared faces")
    try:
        os.remove(out_st)
    except OSError:
        pass

    # Watertight: 6 loose planes that tile a 2x2x2 box must sew into one closed solid.
    def plane(c, n, e1, e2):
        return {"kind": "PLANE", "op": "ADD", "name": "f", "params": {
            "point": c, "normal": n, "e1": e1, "e2": e2, "half_u": 1.0, "half_v": 1.0}}
    box_planes = [
        plane((1, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)),
        plane((-1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, 0, 1)),
        plane((0, 1, 0), (0, 1, 0), (1, 0, 0), (0, 0, 1)),
        plane((0, -1, 0), (0, -1, 0), (1, 0, 0), (0, 0, 1)),
        plane((0, 0, 1), (0, 0, 1), (1, 0, 0), (0, 1, 0)),
        plane((0, 0, -1), (0, 0, -1), (1, 0, 0), (0, 1, 0)),
    ]
    out6 = os.path.join(os.path.dirname(__file__), "occ_watertight.step")
    info = occ_export.export(box_planes, out6, unit="MM", watertight=True, sew_tol=1e-6)
    print("[info] watertight export:", info)
    if "free edge" in info or "watertight" not in info:
        fail(f"planes were not made watertight: {info}")
    r6 = STEPControl_Reader(); r6.ReadFile(out6); r6.TransferRoots(); sh6 = r6.OneShape()
    n6 = 0
    e6 = TopExp_Explorer(sh6, TopAbs_SOLID)
    while e6.More():
        n6 += 1
        e6.Next()
    pr6 = GProp_GProps(); BRepGProp.VolumeProperties_s(sh6, pr6); v6 = pr6.Mass()
    print(f"[info] sewn solid: solids={n6} volume={v6:.3f} (expected 8.0)")
    if n6 != 1 or abs(v6 - 8.0) > 0.01:
        fail(f"6 planes did not sew into one box solid (solids={n6}, vol={v6:.3f})")
    print("[ok] watertight: 6 loose planes → 1 closed box solid (volume 8)")
    try:
        os.remove(out6)
    except OSError:
        pass

    # Leftover MESH_PATCH: replace one side of the box with a 2-triangle faceted
    # patch — the watertight pass must sew it with the analytic planes into the
    # same closed solid.
    patch_side = {"kind": "MESH_PATCH", "op": "ADD", "name": "patch", "params": {
        "verts": [(1, -1, -1), (1, 1, -1), (1, 1, 1), (1, -1, 1)],
        "tris": [(0, 1, 2), (0, 2, 3)]}}
    mixed = [patch_side] + box_planes[1:]
    out7 = os.path.join(os.path.dirname(__file__), "occ_patch.step")
    info7 = occ_export.export(mixed, out7, unit="MM", watertight=True, sew_tol=1e-6)
    print("[info] patch watertight export:", info7)
    r7 = STEPControl_Reader(); r7.ReadFile(out7); r7.TransferRoots(); sh7 = r7.OneShape()
    n7 = 0
    e7 = TopExp_Explorer(sh7, TopAbs_SOLID)
    while e7.More():
        n7 += 1
        e7.Next()
    pr7 = GProp_GProps(); BRepGProp.VolumeProperties_s(sh7, pr7); v7 = pr7.Mass()
    print(f"[info] patched solid: solids={n7} volume={v7:.3f} (expected 8.0)")
    if n7 != 1 or abs(v7 - 8.0) > 0.01:
        fail(f"mesh patch did not sew into the box (solids={n7}, vol={v7:.3f})")
    print("[ok] MESH_PATCH: faceted side sewn with analytic planes → closed solid")
    try:
        os.remove(out7)
    except OSError:
        pass

    # Fillet (#5): a 90° edge fillet exports as a trimmed cylindrical patch.
    from reverse_mesh import step_export
    fillet = [{"kind": "FILLET", "name": "fl", "params": {
        "base": (0, 0, 0), "axis": (0, 0, 1), "ref": (1, 0, 0),
        "radius": 1.0, "height": 4.0, "u_min": 0.0, "u_max": math.pi / 2}}]
    expected_area = 1.0 * (math.pi / 2) * 4.0      # r · span · height

    # OCCT-native path.
    out_fl = os.path.join(os.path.dirname(__file__), "occ_fillet.step")
    occ_export.export(fillet, out_fl, unit="MM")
    rfl = STEPControl_Reader(); rfl.ReadFile(out_fl); rfl.TransferRoots(); shfl = rfl.OneShape()
    nf = 0
    e = TopExp_Explorer(shfl, TopAbs_FACE)
    while e.More():
        nf += 1; e.Next()
    afl = GProp_GProps(); BRepGProp.SurfaceProperties_s(shfl, afl); area_fl = afl.Mass()
    print(f"[info] OCCT fillet: faces={nf} area={area_fl:.3f} expected={expected_area:.3f}")
    if shfl.IsNull() or nf < 1 or abs(area_fl - expected_area) > 0.05:
        fail(f"OCCT fillet patch wrong (faces={nf}, area={area_fl:.3f})")
    print("[ok] OCCT fillet: trimmed cylindrical patch, area correct")

    # Pure-Python path: the hand-written trimmed face must re-read as valid.
    out_flp = os.path.join(os.path.dirname(__file__), "py_fillet.step")
    with open(out_flp, "w") as f:
        f.write(step_export.build_step(fillet, unit="MM", product_name="Fillet"))
    rflp = STEPControl_Reader()
    if rflp.ReadFile(out_flp) != IFSelect_RetDone:
        fail("pure-Python fillet did not re-read")
    rflp.TransferRoots(); shflp = rflp.OneShape()
    aflp = GProp_GProps(); BRepGProp.SurfaceProperties_s(shflp, aflp); area_p = aflp.Mass()
    print(f"[info] pure-Python fillet: valid={BRepCheck_Analyzer(shflp).IsValid()} area={area_p:.3f}")
    if shflp.IsNull() or not BRepCheck_Analyzer(shflp).IsValid() or abs(area_p - expected_area) > 0.05:
        fail(f"pure-Python fillet patch invalid (area={area_p:.3f})")
    print("[ok] pure-Python fillet: trimmed patch re-reads valid, area correct")
    for f in (out_fl, out_flp):
        try:
            os.remove(f)
        except OSError:
            pass

    # Semantic PMI (#11b): a PMI-embedded pure-Python STEP must still re-read and
    # its geometry import cleanly (the dimensions ride alongside, not breaking it).
    pmi_feats = [
        {"kind": "CYLINDER", "name": "c", "params": {
            "base": (0, 0, 0), "axis": (0, 0, 1), "radius": 2.0, "height": 6.0}},
        {"kind": "SPHERE", "name": "s", "params": {"center": (10, 0, 0), "radius": 2.5}},
    ]
    out_pmi = os.path.join(os.path.dirname(__file__), "py_pmi.step")
    with open(out_pmi, "w") as f:
        f.write(step_export.build_step(pmi_feats, unit="MM", product_name="PMI", pmi=True))
    rpmi = STEPControl_Reader()
    if rpmi.ReadFile(out_pmi) != IFSelect_RetDone:
        fail("PMI-embedded STEP did not re-read")
    rpmi.TransferRoots(); shpmi = rpmi.OneShape()
    npmi = 0
    e = TopExp_Explorer(shpmi, TopAbs_SOLID)
    while e.More():
        npmi += 1; e.Next()
    if shpmi.IsNull() or npmi != 2:
        fail(f"PMI embedding broke geometry import (solids={npmi})")
    if "DIMENSIONAL_SIZE(" not in open(out_pmi).read():
        fail("semantic dimensions missing from PMI STEP")
    print(f"[ok] semantic PMI: dimensions embedded, geometry still imports ({npmi} solids)")
    try:
        os.remove(out_pmi)
    except OSError:
        pass

    # Extrude (#12): a stadium-slot prism must export as an exact solid on both
    # backends (volume = (8 + π) · h), and cut as a pocket via booleans.
    stadium = {"kind": "EXTRUDE", "name": "slot", "params": {
        "base": (0, 0, 0), "axis": (0, 0, 1), "xdir": (1, 0, 0), "height": 1.5,
        "profile": [
            [0, -2, -1, 2, -1, 0, 0, 0],
            [1, 2, -1, 2, 1, 2, 0, 1],
            [0, 2, 1, -2, 1, 0, 0, 0],
            [1, -2, 1, -2, -1, -2, 0, 1],
        ]}}
    vol_expected = (8.0 + math.pi) * 1.5
    out_ex = os.path.join(os.path.dirname(__file__), "occ_extrude.step")
    info_ex = occ_export.export([stadium], out_ex, unit="MM")
    if not info_ex.solids or abs(info_ex.solids[0]["volume"] - vol_expected) > 1e-6 \
            or not info_ex.solids[0]["valid"]:
        fail(f"OCCT extrude solid wrong: {info_ex.solids}")
    print(f"[ok] OCCT extrude: volume {info_ex.solids[0]['volume']:.6f} "
          f"= (8+π)·1.5, valid")

    # Pure-Python extrude: hand-written closed B-rep must re-read valid with the
    # same exact volume (this exercises the shared-edge shell and concave arcs).
    notch = {"kind": "EXTRUDE", "name": "notch", "params": {
        "base": (0, 0, 0), "axis": (0, 0, 1), "xdir": (1, 0, 0), "height": 1.0,
        "profile": [
            [0, -2, -2, -1, -2, 0, 0, 0],
            [1, -1, -2, 1, -2, 0, -2, 0],      # concave (cw) arc notch
            [0, 1, -2, 2, -2, 0, 0, 0],
            [0, 2, -2, 2, 2, 0, 0, 0],
            [0, 2, 2, -2, 2, 0, 0, 0],
            [0, -2, 2, -2, -2, 0, 0, 0],
        ]}}
    out_exp = os.path.join(os.path.dirname(__file__), "py_extrude.step")
    with open(out_exp, "w") as f:
        f.write(step_export.build_step([stadium, notch], unit="MM", product_name="Ex"))
    rex = STEPControl_Reader()
    if rex.ReadFile(out_exp) != IFSelect_RetDone:
        fail("pure-Python extrude did not re-read")
    rex.TransferRoots(); shex = rex.OneShape()
    if shex.IsNull() or not BRepCheck_Analyzer(shex).IsValid():
        fail("pure-Python extrude re-read invalid")
    vex = GProp_GProps(); BRepGProp.VolumeProperties_s(shex, vex)
    vol_both = vol_expected + (16.0 - math.pi / 2.0) * 1.0
    if abs(vex.Mass() - vol_both) > 1e-6:
        fail(f"pure-Python extrude volume {vex.Mass():.6f} != {vol_both:.6f}")
    print(f"[ok] pure-Python extrude: 2 solids re-read valid, volume exact "
          f"({vex.Mass():.6f})")

    # Extrude as a SUBTRACT cutter: hexagonal pocket milled from a box.
    hexp = [[0, math.cos(2 * math.pi * i / 6), math.sin(2 * math.pi * i / 6),
             math.cos(2 * math.pi * (i + 1) / 6), math.sin(2 * math.pi * (i + 1) / 6),
             0, 0, 0] for i in range(6)]
    base_box = {"kind": "BOX", "name": "b", "params": {
        "center": (0, 0, 0), "ax": (1, 0, 0), "ay": (0, 1, 0), "az": (0, 0, 1),
        "hx": 3.0, "hy": 3.0, "hz": 1.0}}
    hex_cut = {"kind": "EXTRUDE", "name": "pocket", "op": "SUBTRACT", "params": {
        "base": (0, 0, -1.0), "axis": (0, 0, 1), "xdir": (1, 0, 0), "height": 2.0,
        "profile": hexp}}
    out_cut = os.path.join(os.path.dirname(__file__), "occ_extrude_cut.step")
    info_cut = occ_export.export([base_box, hex_cut], out_cut, unit="MM", merge=True)
    hex_area = 6.0 * math.sqrt(3.0) / 4.0
    vol_cut = 6.0 * 6.0 * 2.0 - hex_area * 2.0
    if not info_cut.solids or abs(info_cut.solids[0]["volume"] - vol_cut) > 1e-6:
        fail(f"extrude cutter wrong: {info_cut.solids} expected {vol_cut}")
    print(f"[ok] extrude as cutter: hex pocket volume exact "
          f"({info_cut.solids[0]['volume']:.6f})")
    for f in (out_ex, out_exp, out_cut):
        try:
            os.remove(f)
        except OSError:
            pass

    # Fillet blends (#13): a recognized FILLET applied as a true round on the
    # solid's edge — volume drops by exactly (r² − πr²/4)·h, body stays valid
    # and closed; a fillet with no matching edge falls back to a patch.
    rb = 0.5
    blend_box = {"kind": "BOX", "name": "b", "params": {
        "center": (0, 0, 0), "ax": (1, 0, 0), "ay": (0, 1, 0), "az": (0, 0, 1),
        "hx": 2.0, "hy": 2.0, "hz": 2.0}}
    blend_fil = {"kind": "FILLET", "name": "f", "params": {
        "base": (2 - rb, 2 - rb, 0.0), "axis": (0, 0, 1), "ref": (1, 0, 0),
        "radius": rb, "height": 4.0, "u_min": 0.0, "u_max": math.pi / 2}}
    out_bl = os.path.join(os.path.dirname(__file__), "occ_blend.step")
    info_bl = occ_export.export([blend_box, blend_fil], out_bl, unit="MM",
                                merge=True, blend_fillets=True)
    vol_bl = 64.0 - (rb * rb - math.pi * rb * rb / 4.0) * 4.0
    if "1 fillet blend(s)" not in str(info_bl):
        fail(f"fillet blend not applied: {info_bl}")
    if not info_bl.solids or abs(info_bl.solids[0]["volume"] - vol_bl) > 1e-6 \
            or not info_bl.solids[0]["valid"]:
        fail(f"fillet blend volume wrong: {info_bl.solids} expected {vol_bl}")
    print(f"[ok] fillet blend: rounded solid volume exact "
          f"({info_bl.solids[0]['volume']:.6f}), valid")
    far_fil = {"kind": "FILLET", "name": "f2", "params": {
        "base": (30.0, 30.0, 0.0), "axis": (0, 0, 1), "ref": (1, 0, 0),
        "radius": rb, "height": 4.0, "u_min": 0.0, "u_max": math.pi / 2}}
    info_um = occ_export.export([blend_box, far_fil], out_bl, unit="MM",
                                merge=True, blend_fillets=True)
    if "unmatched" not in str(info_um):
        fail(f"unmatched fillet should fall back to a patch: {info_um}")
    if not info_um.solids or abs(info_um.solids[0]["volume"] - 64.0) > 1e-6:
        fail(f"unmatched fillet altered the solid: {info_um.solids}")
    print("[ok] unmatched fillet falls back to a trimmed patch, solid untouched")
    try:
        os.remove(out_bl)
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
