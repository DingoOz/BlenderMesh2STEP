# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender background test for the forward Build — STEP Primitives workflow.

Run with:
    blender --background --python reverse_mesh/tests/test_forward.py
Exits non-zero on failure.
"""

import os
import sys

import bpy

PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(PKG_DIR))

import reverse_mesh  # noqa: E402
from reverse_mesh import forward  # noqa: E402
from reverse_mesh.operators import _PARAM_KINDS  # noqa: E402
from reverse_mesh.properties import build_params_synced  # noqa: E402


def fail(msg):
    print(f"[FAIL] {msg}")
    sys.exit(1)


DIMS = {
    "BOX": {"hx": 1.0, "hy": 2.0, "hz": 3.0},
    "CYLINDER": {"radius": 2.0, "height": 6.0},
    "CONE": {"radius1": 3.0, "radius2": 1.0, "height": 4.0},
    "SPHERE": {"radius": 2.5},
    "TORUS": {"major_radius": 4.0, "minor_radius": 1.0},
    "EXTRUDE": {"radius": 1.5, "height": 3.0, "sides": 8},
    "REVOLVE": {"radius1": 1.0, "radius2": 2.0, "height": 0.5},
}


def main():
    reverse_mesh.register()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    settings = scene.reverse
    print("[ok] registered")

    # --- create every kind --------------------------------------------------
    scene.cursor.location = (1.0, 2.0, 3.0)
    for kind in forward.BUILD_KINDS:
        if bpy.ops.reverse.add_primitive(kind=kind, **DIMS[kind]) != {"FINISHED"}:
            fail(f"add_primitive({kind}) failed")
        obj = bpy.context.active_object
        data = obj.get("reverse")
        if data is None:
            fail(f"{kind}: no obj['reverse'] params")
        schema = _PARAM_KINDS[kind]
        for key in schema["points"] + schema["dirs"] + schema["lengths"]:
            if key == "apex":            # optional in the export schema
                continue
            if key not in data.keys():
                fail(f"{kind}: param '{key}' missing on built object")
        if data["group"] != "BUILD":
            fail(f"{kind}: group is {data['group']}, expected BUILD")
        feat = settings.features[settings.active_feature]
        if feat.object_name != obj.name or feat.group != "BUILD":
            fail(f"{kind}: feature stack entry missing or untagged")
        if not build_params_synced(obj):
            fail(f"{kind}: panel fields not synced after creation")
        if forward.drift_status(obj) is not None:
            fail(f"{kind}: fresh build reports drift: {forward.drift_status(obj)}")
        print(f"[ok] built {kind}: {feat.summary}")
    if len(settings.features) != len(forward.BUILD_KINDS):
        fail(f"feature stack has {len(settings.features)} entries")

    # --- live parametric edit ------------------------------------------------
    bpy.ops.reverse.add_primitive(kind="CYLINDER", radius=2.0, height=6.0)
    cyl = bpy.context.active_object
    old_x = max(v.co.x for v in cyl.data.vertices)
    cyl.reverse_build.radius = 3.5
    data = cyl.get("reverse")
    if abs(data["radius"] - 3.5) > 1e-9:
        fail("radius edit did not write through to obj['reverse']")
    new_x = max(v.co.x for v in cyl.data.vertices)
    if abs(new_x - 3.5) > 1e-4 or abs(new_x - old_x) < 1.0:
        fail(f"mesh not regenerated after edit (max x {old_x} → {new_x})")
    feat = next(f for f in settings.features if f.object_name == cyl.name)
    if "3.5" not in feat.summary:
        fail(f"stack summary not refreshed: {feat.summary}")
    if forward.drift_status(cyl) is not None:
        fail(f"clean edit reports drift: {forward.drift_status(cyl)}")
    print("[ok] live edit: radius 2.0 → 3.5 regenerated mesh, dict and summary")

    # --- drift detection + rebuild -------------------------------------------
    cyl.data.vertices[0].co.x += 0.25
    if forward.drift_status(cyl) is None:
        fail("vertex edit not detected as drift")
    if bpy.ops.reverse.rebuild_feature() != {"FINISHED"}:
        fail("rebuild_feature failed")
    if forward.drift_status(cyl) is not None:
        fail("drift persists after rebuild")
    print("[ok] drift detected and cleared by Rebuild from Parameters")

    # --- moved object keeps its placement across a rebuild --------------------
    cyl.location.x += 5.0
    bpy.context.view_layer.update()
    moved_x = cyl.matrix_world.translation.x
    cyl.reverse_build.height = 8.0
    bpy.context.view_layer.update()
    if abs(cyl.matrix_world.translation.x - moved_x) > 1e-6:
        fail("rebuild lost the user's move")
    print("[ok] rebuild preserves a manual move")

    # --- scale handling --------------------------------------------------------
    cyl.scale = (2.0, 2.0, 2.0)
    bpy.context.view_layer.update()
    r_before = float(cyl["reverse"]["radius"])
    if bpy.ops.reverse.bake_scale() != {"FINISHED"}:
        fail("bake_scale failed")
    if abs(float(cyl["reverse"]["radius"]) - 2.0 * r_before) > 1e-9:
        fail("uniform scale not baked into radius")
    if any(abs(c - 1.0) > 1e-9 for c in cyl.scale):
        fail("scale not reset after bake")
    cyl.scale = (1.0, 2.0, 1.0)
    bpy.context.view_layer.update()
    if forward.drift_status(cyl) is None:
        fail("non-uniform scale on a cylinder not flagged")
    cyl.scale = (1.0, 1.0, 1.0)
    bpy.context.view_layer.update()
    print("[ok] uniform scale bakes, non-uniform scale flagged")

    # --- extrude: profile follows a radius edit and a baked scale --------------
    bpy.ops.reverse.add_primitive(kind="EXTRUDE", sides=6, radius=1.0, height=2.0)
    import math as _m
    prism = bpy.context.active_object
    prism.reverse_build.radius = 2.0
    r_prof = _m.hypot(prism["reverse"]["profile"][0][1],
                      prism["reverse"]["profile"][0][2])
    if abs(r_prof - 2.0) > 1e-9:
        fail(f"extrude profile did not follow radius edit ({r_prof})")
    prism.scale = (1.5, 1.5, 1.5)
    bpy.context.view_layer.update()
    if bpy.ops.reverse.bake_scale() != {"FINISHED"}:
        fail("extrude bake_scale failed")
    r_prof = _m.hypot(prism["reverse"]["profile"][0][1],
                      prism["reverse"]["profile"][0][2])
    if abs(r_prof - 3.0) > 1e-9 or abs(prism["reverse"]["height"] - 3.0) > 1e-9:
        fail(f"extrude bake wrong (profile r {r_prof}, h {prism['reverse']['height']})")
    print("[ok] extrude profile follows radius edit and baked scale")

    # --- export: ADD box + SUBTRACT cylinder, pure-Python writer ---------------
    bpy.ops.wm.read_factory_settings(use_empty=True)
    settings = bpy.context.scene.reverse
    settings.default_operation = "ADD"
    bpy.ops.reverse.add_primitive(kind="BOX", hx=5.0, hy=5.0, hz=2.0)
    settings.default_operation = "SUBTRACT"
    bpy.ops.reverse.add_primitive(kind="CYLINDER", radius=1.5, height=10.0)
    hole = bpy.context.active_object
    if hole["reverse"]["op"] != "SUBTRACT":
        fail("SUBTRACT role not recorded on built cutter")

    path = os.path.join(PKG_DIR, "tests", "smoke_forward.step")
    res = bpy.ops.reverse.export_step(filepath=path, backend="PUREPYTHON",
                                      scale_mode="MANUAL", scale=1.0,
                                      group_filter="BUILD", py_cutters="MARK")
    if res != {"FINISHED"}:
        fail("export_step failed")
    text = open(path, encoding="ascii").read()
    for needle in ("CYLINDRICAL_SURFACE", "MANIFOLD_SOLID_BREP", "ISO-10303-21"):
        if needle not in text:
            fail(f"exported STEP missing {needle}")
    if "1.5" not in text:
        fail("exported STEP missing the absolute cylinder radius 1.5")
    # No dangling entity references (mirrors test_step.py's structural check).
    import re
    defined = set(re.findall(r"^#(\d+)\s*=", text, re.M))
    used = set(re.findall(r"#(\d+)", text))
    if not used <= defined:
        fail(f"dangling STEP refs: {sorted(used - defined)[:10]}")
    print(f"[ok] exported BUILD set → {os.path.basename(path)} ({len(text)} chars)")

    reverse_mesh.unregister()
    print("[ok] unregistered")
    print("ALL FORWARD BUILD TESTS PASSED")


if __name__ == "__main__":
    main()
