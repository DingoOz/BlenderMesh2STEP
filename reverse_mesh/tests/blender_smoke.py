# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender background smoke test: register the add-on and fit a real mesh.

Run with:
    blender --background --python reverse_mesh/tests/blender_smoke.py
Exits non-zero on failure.
"""

import os
import sys

import bpy
import bmesh

# Make the parent of the package importable, then import as a package so the
# relative imports inside the add-on resolve.
PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(PKG_DIR))

import reverse_mesh  # noqa: E402


def fail(msg):
    print(f"[FAIL] {msg}")
    sys.exit(1)


def main():
    reverse_mesh.register()
    print("[ok] registered")

    # Clean scene, add a cylinder primitive (a genuine analytic shape to recover).
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_cylinder_add(radius=2.0, depth=6.0, vertices=64)
    obj = bpy.context.active_object

    # Edit mode. First check Select Similar (#9): one side quad must grow to the
    # whole wall (64 side quads) without leaking onto the two n-gon caps.
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    for f in bm.faces:
        f.select_set(False)
    seed = next(f for f in bm.faces if len(f.verts) == 4)
    seed.select_set(True)
    bm.faces.active = seed
    bmesh.update_edit_mesh(obj.data)
    if bpy.ops.reverse.select_similar() != {"FINISHED"}:
        fail("select_similar operator failed")
    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    grown = [f for f in bm.faces if f.select]
    if len(grown) != 64 or any(len(f.verts) != 4 for f in grown):
        fail(f"select_similar grew to {len(grown)} faces (expected 64 side quads, no caps)")
    print(f"[ok] select-similar grew one face → {len(grown)} wall faces (caps excluded)")

    # Now select only the side faces (exclude the two n-gon caps) to fit.
    for f in bm.faces:
        f.select_set(len(f.verts) == 4)  # side quads only
    bmesh.update_edit_mesh(obj.data)

    settings = bpy.context.scene.reverse
    settings.primitive_type = "CYLINDER"
    settings.create_object = True

    result = bpy.ops.reverse.fit_selection()
    if result != {"FINISHED"}:
        fail(f"operator returned {result}")

    feats = bpy.context.scene.reverse.features
    if len(feats) != 1:
        fail(f"expected 1 feature, got {len(feats)}")
    item = feats[0]
    print(f"[ok] fitted {item.kind}: {item.summary}  rms={item.rms:.3e}")

    if item.kind != "CYLINDER":
        fail(f"expected CYLINDER, got {item.kind}")
    if item.rms > 1e-4:
        fail(f"cylinder RMS too high: {item.rms}")
    created = bpy.data.objects.get(item.object_name)
    if created is None:
        fail("clean object was not created")
    if "reverse" not in created:
        fail("fit params not stored on created object")
    params = created["reverse"]
    if abs(params["radius"] - 2.0) > 1e-3:
        fail(f"recovered radius {params['radius']} != 2.0")

    print(f"[ok] created object '{created.name}' with stored params radius={params['radius']:.4f}")

    # AUTO path should also identify the cylinder.
    settings.primitive_type = "AUTO"
    bpy.ops.reverse.fit_selection()
    autofeat = bpy.context.scene.reverse.features[-1]
    if autofeat.kind != "CYLINDER":
        fail("AUTO did not detect cylinder")
    # Fit confidence (#2): AUTO records a winner-first runner-up summary.
    if not autofeat.runner_up.startswith("CYLINDER") or "|" not in autofeat.runner_up:
        fail(f"runner-up summary missing/malformed: '{autofeat.runner_up}'")
    print(f"[ok] AUTO detected CYLINDER · confidence: {autofeat.runner_up}")

    # Dimension snapping (#3): with snap on, the r≈2 fit must store exactly 2.0.
    settings.primitive_type = "CYLINDER"
    settings.snap_enabled = True
    settings.snap_preset = "0.5"
    bpy.ops.reverse.fit_selection()
    snapfeat = bpy.context.scene.reverse.features[-1]
    snapobj = bpy.data.objects.get(snapfeat.object_name)
    if snapobj["reverse"]["radius"] != 2.0:
        fail(f"snap did not land radius on 2.0: {snapobj['reverse']['radius']}")
    print(f"[ok] snapping stored exact radius {snapobj['reverse']['radius']}")
    settings.snap_enabled = False

    # Fit-quality heatmap (#1): fitting with it on registers overlay geometry;
    # switching it off clears the overlay.
    from reverse_mesh import overlay as _ov
    settings.show_heatmap = True
    bpy.ops.reverse.fit_selection()
    if not any(k.startswith("heatmap:") for k in _ov.active_keys()):
        fail("heatmap on: no overlay was registered after a fit")
    print(f"[ok] heatmap overlay registered ({len(_ov.active_keys())} key(s))")
    settings.show_heatmap = False        # update callback clears it
    if any(k.startswith("heatmap:") for k in _ov.active_keys()):
        fail("heatmap off: overlay was not cleared")
    print("[ok] heatmap cleared when toggled off")

    # Fillet (#5): a 90° arc of a cylinder wall fits as a trimmed partial cylinder.
    import math as _math
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.mesh.primitive_cylinder_add(radius=1.5, depth=4.0, vertices=48,
                                        location=(0.0, 30.0, 0.0))
    fobj = bpy.context.active_object
    bpy.ops.object.mode_set(mode="EDIT")
    fbm = bmesh.from_edit_mesh(fobj.data)
    fbm.faces.ensure_lookup_table()
    for f in fbm.faces:
        c = f.calc_center_median()                    # local coords (origin-centred)
        ang = _math.atan2(c.y, c.x)                   # face angle about the cylinder axis
        f.select_set(len(f.verts) == 4 and -0.05 <= ang <= _math.pi / 2 + 0.05)
    bmesh.update_edit_mesh(fobj.data)
    settings.primitive_type = "FILLET"
    settings.segment_regions = False
    if bpy.ops.reverse.fit_selection() != {"FINISHED"}:
        fail("fillet fit failed")
    bpy.ops.object.mode_set(mode="OBJECT")
    ffeat = bpy.context.scene.reverse.features[-1]
    fco = bpy.data.objects.get(ffeat.object_name)
    span = _math.degrees(fco["reverse"]["u_max"] - fco["reverse"]["u_min"])
    if ffeat.kind != "FILLET" or abs(fco["reverse"]["radius"] - 1.5) > 1e-2 or not (70 < span < 110):
        fail(f"fillet fit wrong: {ffeat.kind} r={fco['reverse']['radius']:.3f} span={span:.1f}°")
    print(f"[ok] fillet fit: r={fco['reverse']['radius']:.3f} span={span:.0f}° ({ffeat.summary})")
    settings.primitive_type = "AUTO"

    # Torus: whole-mesh fit through the same pipeline.
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.mesh.primitive_torus_add(major_radius=5.0, minor_radius=1.2,
                                     major_segments=64, minor_segments=24)
    tor = bpy.context.active_object
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    settings.primitive_type = "TORUS"
    if bpy.ops.reverse.fit_selection() != {"FINISHED"}:
        fail("torus fit failed")
    tfeat = bpy.context.scene.reverse.features[-1]
    print(f"[ok] fitted {tfeat.kind}: {tfeat.summary}  rms={tfeat.rms:.3e}")
    if tfeat.kind != "TORUS" or tfeat.rms > 1e-3:
        fail(f"torus fit poor: {tfeat.kind} rms={tfeat.rms}")
    tobj = bpy.data.objects.get(tfeat.object_name)
    if abs(tobj["reverse"]["major_radius"] - 5.0) > 1e-2:
        fail(f"torus major radius {tobj['reverse']['major_radius']} != 5.0")
    print(f"[ok] torus R={tobj['reverse']['major_radius']:.4f} r={tobj['reverse']['minor_radius']:.4f}")
    bpy.ops.object.mode_set(mode="OBJECT")

    # Cube with segmentation must become 6 planes, not one sphere.
    n_before = len(bpy.context.scene.reverse.features)
    bpy.ops.mesh.primitive_cube_add(size=2.0)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    settings.primitive_type = "AUTO"
    settings.segment_regions = True
    settings.segment_angle = 20.0
    if bpy.ops.reverse.fit_selection() != {"FINISHED"}:
        fail("cube segmentation fit failed")
    new = bpy.context.scene.reverse.features[n_before:]
    kinds = [f.kind for f in new]
    print(f"[ok] cube segmented into {len(new)} regions: {kinds}")
    if len(kinds) != 6 or any(k != "PLANE" for k in kinds):
        fail(f"expected 6 planes, got {kinds}")
    print("[ok] cube → 6 planes (not a sphere)")
    bpy.ops.object.mode_set(mode="OBJECT")

    # Whole cube, no segmentation: AUTO must now pick a BOX (not torus/sphere).
    n0 = len(bpy.context.scene.reverse.features)
    bpy.ops.mesh.primitive_cube_add(size=2.0)
    bpy.ops.transform.rotate(value=0.6, orient_axis="Z")   # rotate to test orientation
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    settings.primitive_type = "AUTO"
    settings.segment_regions = False
    if bpy.ops.reverse.fit_selection() != {"FINISHED"}:
        fail("box AUTO fit failed")
    bfeat = bpy.context.scene.reverse.features[n0]
    print(f"[ok] rotated cube AUTO → {bfeat.kind}: {bfeat.summary}")
    if bfeat.kind != "BOX":
        fail(f"expected BOX, got {bfeat.kind}")
    bobj = bpy.data.objects.get(bfeat.object_name)
    dims = sorted(round(d, 3) for d in (bobj["reverse"]["hx"], bobj["reverse"]["hy"], bobj["reverse"]["hz"]))
    if dims != [1.0, 1.0, 1.0]:
        fail(f"box half-extents wrong: {dims}")
    print(f"[ok] box half-extents {dims} (rotation recovered)")
    bpy.ops.object.mode_set(mode="OBJECT")

    # Feature stack (#7): reorder, re-fit, remove, and load-time reconcile.
    feats = bpy.context.scene.reverse.features
    n_feats = len(feats)
    if n_feats < 2:
        fail("need at least 2 features to test the stack")
    # Reorder: swap the first two and confirm the order changes.
    k0, k1 = feats[0].kind, feats[1].kind
    bpy.context.scene.reverse.active_feature = 0
    if bpy.ops.reverse.move_feature(direction="DOWN") != {"FINISHED"}:
        fail("move_feature failed")
    if feats[0].kind != k1 or feats[1].kind != k0:
        fail(f"reorder did not swap: {[f.kind for f in feats][:2]}")
    print(f"[ok] feature reorder swapped {k0}↔{k1}")

    # Re-fit: pick a feature with stored source faces and regenerate its object.
    ri = next((i for i, f in enumerate(feats)
               if f.source_object and f.source_faces and f.object_name), None)
    if ri is None:
        fail("no feature carried source faces for re-fit")
    bpy.context.scene.reverse.active_feature = ri
    old_name = feats[ri].object_name
    old_kind = feats[ri].kind
    if bpy.ops.reverse.refit_feature() != {"FINISHED"}:
        fail("refit_feature failed")
    if feats[ri].kind != old_kind:
        fail(f"re-fit changed kind {old_kind}→{feats[ri].kind}")
    if bpy.data.objects.get(old_name) is not None and feats[ri].object_name == old_name:
        fail("re-fit did not rebuild the object")
    print(f"[ok] re-fit regenerated {old_kind} object")

    # Remove: deletes the entry and its clean object.
    bpy.context.scene.reverse.active_feature = ri
    victim = feats[ri].object_name
    if bpy.ops.reverse.remove_feature() != {"FINISHED"}:
        fail("remove_feature failed")
    if len(feats) != n_feats - 1 or bpy.data.objects.get(victim) is not None:
        fail("remove_feature left the entry or object behind")
    print(f"[ok] removed feature + object ({len(feats)} left)")

    # Reconcile: clearing the list then reconciling rebuilds it from objects.
    from reverse_mesh.operators import _reconcile_scene
    n_objs = sum(1 for o in bpy.context.scene.objects
                 if o.type == "MESH" and "reverse" in o)
    feats.clear()
    _reconcile_scene(bpy.context.scene)
    if len(feats) != n_objs:
        fail(f"reconcile rebuilt {len(feats)} features, expected {n_objs} objects")
    print(f"[ok] reconcile rebuilt {len(feats)} features from scene objects")

    # Pattern propagation (#8): three identical cylinders in one mesh. Fit one,
    # propagate, and the other two must be found and fitted.
    bpy.ops.object.mode_set(mode="OBJECT")
    holes = []
    for x in (0.0, 5.0, 10.0):
        bpy.ops.mesh.primitive_cylinder_add(radius=1.0, depth=4.0, vertices=48,
                                            location=(x, -20.0, 0.0))
        holes.append(bpy.context.active_object)
    bpy.ops.object.select_all(action="DESELECT")
    for o in holes:
        o.select_set(True)
    bpy.context.view_layer.objects.active = holes[0]
    bpy.ops.object.join()
    holes_obj = bpy.context.active_object

    bpy.ops.object.mode_set(mode="EDIT")
    hbm = bmesh.from_edit_mesh(holes_obj.data)
    hbm.faces.ensure_lookup_table()
    for f in hbm.faces:
        f.select_set(False)
    seedf = min((f for f in hbm.faces if len(f.verts) == 4),
                key=lambda f: f.calc_center_median().x)
    seedf.select_set(True)
    hbm.faces.active = seedf
    bmesh.update_edit_mesh(holes_obj.data)
    bpy.ops.reverse.select_similar()              # grow to one cylinder wall
    settings.primitive_type = "CYLINDER"
    settings.segment_regions = False
    bpy.ops.reverse.fit_selection()               # records the seed feature
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.context.scene.reverse.active_feature = len(feats) - 1
    before_prop = len(feats)
    if bpy.ops.reverse.propagate_pattern() != {"FINISHED"}:
        fail("propagate_pattern failed")
    made = len(feats) - before_prop
    if made != 2:
        fail(f"pattern propagation created {made} holes, expected 2")
    print(f"[ok] pattern propagation found {made} matching holes from 1 seed")

    # Thread tagging (#12): set a thread spec on a cylinder feature; it must
    # round-trip onto the object and into the exported STEP.
    cyl_feat = next((f for f in feats if f.kind == "CYLINDER" and f.object_name), None)
    if cyl_feat is None:
        fail("no cylinder feature to thread-tag")
    cyl_feat.thread_spec = "M8x1.25"               # fires the update callback
    cyl_obj = bpy.data.objects.get(cyl_feat.object_name)
    if cyl_obj["reverse"].get("thread_spec") != "M8x1.25":
        fail("thread spec did not round-trip onto the object")
    print("[ok] thread spec tagged and stored on object")

    # Counterbore preset (#6): params round-trip onto the object for OCCT export.
    cyl_feat.hole_preset = "COUNTERBORE"
    cyl_feat.cbore_radius = 1.5
    cyl_feat.cbore_depth = 1.0
    if cyl_obj["reverse"].get("hole_preset") != "COUNTERBORE" \
            or abs(cyl_obj["reverse"].get("cbore_radius", 0) - 1.5) > 1e-6:
        fail("counterbore preset did not round-trip onto the object")
    cyl_feat.hole_preset = "NONE"     # clearing removes the keys
    if "hole_preset" in cyl_obj["reverse"]:
        fail("clearing hole preset left keys on the object")
    print("[ok] counterbore preset round-trips and clears")

    # STEP export of everything fitted so far.
    out = os.path.join(os.path.dirname(__file__), "smoke_export.step")
    n_reverse = sum(1 for o in bpy.context.scene.objects if "reverse" in o)
    res = bpy.ops.reverse.export_step(filepath=out, unit="MM", write_pmi_sidecar=True)
    if res != {"FINISHED"}:
        fail(f"STEP export returned {res}")
    # PMI sidecar (#11a) must be written alongside the STEP.
    pmi_json = out[:-len(".step")] + ".pmi.json"
    if not os.path.exists(pmi_json) or os.path.getsize(pmi_json) < 50:
        fail("PMI sidecar .pmi.json missing or empty")
    import json as _json
    with open(pmi_json) as f:
        pmi = _json.load(f)
    if not pmi.get("features"):
        fail("PMI sidecar has no features")
    print(f"[ok] PMI sidecar written ({len(pmi['features'])} features, "
          f"{len(pmi.get('relationships', []))} relationships)")
    if not os.path.exists(out) or os.path.getsize(out) < 500:
        fail("STEP file missing or too small")
    with open(out) as f:
        head = f.read(4000)
    if "ISO-10303-21" not in head or "AP242_MANAGED_MODEL_BASED_3D_ENGINEERING_MIM_LF" not in head:
        fail("STEP file missing AP242 header")
    print(f"[ok] exported STEP for {n_reverse} Reverse objects → {os.path.basename(out)}")

    # Overlay manager (INFRA-B): enable/disable must be leak-free — exactly one
    # draw handler while active, none after clearing.
    from reverse_mesh import overlay
    tri = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
    col = [(1, 0, 0, 1)] * 3
    overlay.set_tris("smoke:tris", tri, col)
    overlay.set_lines("smoke:lines", [(0, 0, 0), (1, 1, 1)], [(0, 1, 0, 1)] * 2)
    if sorted(overlay.active_keys()) != ["smoke:lines", "smoke:tris"]:
        fail(f"overlay keys wrong: {overlay.active_keys()}")
    if overlay._handle is None:
        fail("draw handler not installed while overlays active")
    overlay.clear("smoke:tris")
    if overlay.active_keys() != ["smoke:lines"]:
        fail("overlay clear(key) did not remove just one")
    overlay.clear_all()
    if overlay.active_keys() or overlay._handle is not None:
        fail("overlay clear_all left state behind (handler leak)")
    print("[ok] overlay manager enable/clear leak-free")

    reverse_mesh.unregister()
    print("[ok] unregistered")
    print("\nALL BLENDER SMOKE CHECKS PASSED")


if __name__ == "__main__":
    main()
