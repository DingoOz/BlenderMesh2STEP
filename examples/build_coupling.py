# SPDX-License-Identifier: GPL-3.0-or-later
"""Generate the flange-coupling worked example and its final STEP.

A square flange coupling built entirely from BlenderMesh2STEP features, to show
how they combine — Add/Subtract booleans — into one watertight CAD solid:

    01 Plate        EXTRUDE  Add       rounded-square base (lines + arcs)
    02 Hub          CYLINDER Add       central boss
    03 Bore         CYLINDER Subtract  through hole
    04 Keyway       BOX      Subtract  key slot in the bore
    05 Relief       REVOLVE  Subtract  turned undercut ring at the hub base
    06 Bolt 1..4    CYLINDER Subtract  counterbored corner holes (a pattern)

Run headless from the repo root (writes the .blend and, if OCCT is present,
the merged .step + validation report):

    blender --background --python examples/build_coupling.py
"""

import math
import os
import sys

import bpy

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import reverse_mesh  # noqa: E402
from reverse_mesh import build, forward, occ_export  # noqa: E402
from reverse_mesh.fitting.common import FitResult  # noqa: E402
from reverse_mesh.fitting.primitives import summarize  # noqa: E402

reverse_mesh.register()

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.unit_settings.system = "METRIC"
scene.unit_settings.length_unit = "MILLIMETERS"
scene.unit_settings.scale_length = 0.001

COLL = bpy.data.collections.new("Flange Coupling")
scene.collection.children.link(COLL)
EXPLODE = bpy.data.collections.new("How it's made (exploded, display only)")
scene.collection.children.link(EXPLODE)


def move_to(obj, coll):
    for c in list(obj.users_collection):
        c.objects.unlink(obj)
    coll.objects.link(obj)


def label(text, loc, coll, size=1.6):
    cu = bpy.data.curves.new(text, type="FONT")
    cu.body = text
    cu.size = size
    cu.align_x = "CENTER"
    ob = bpy.data.objects.new(f"txt: {text}", cu)
    ob.location = loc
    ob.rotation_euler = (math.radians(90), 0, 0)
    coll.objects.link(ob)
    return ob


_features = []                       # (name, feature-object) in build order


def feature(kind, params, name, operation="ADD", cut="THROUGH", extra=None):
    result = FitResult(kind=kind, rms=0.0, max_error=0.0, params=params,
                       summary=summarize(kind, params))
    obj = build.build_object(bpy.context, result, segments=72,
                             operation=operation, cut_mode=cut)
    data = {k: obj["reverse"][k] for k in obj["reverse"].keys()}
    data["group"] = "BUILD"
    if extra:
        data.update(extra)
    obj["reverse"] = data
    obj.name = name
    obj.data.name = name
    obj.show_name = True
    obj.color = ((0.85, 0.25, 0.25, 1.0) if operation == "SUBTRACT"
                 else (0.75, 0.75, 0.80, 1.0))
    move_to(obj, COLL)
    item = scene.reverse.features.add()
    item.kind, item.summary, item.object_name = kind, result.summary, obj.name
    item.operation, item.cut_mode, item.group = operation, cut, "BUILD"
    _features.append((name, obj))
    return obj


def rounded_square(h, r):
    """CCW (S,8) profile of a square (half-side h) with corner radius r."""
    L, A = 0.0, 1.0
    def line(s, e):
        return [L, s[0], s[1], e[0], e[1], 0.0, 0.0, 0.0]
    def arc(s, e, c):
        return [A, s[0], s[1], e[0], e[1], c[0], c[1], 1.0]
    a = h - r
    return [
        line((h, -a), (h, a)),
        arc((h, a), (a, h), (a, a)),
        line((a, h), (-a, h)),
        arc((-a, h), (-h, a), (-a, a)),
        line((-h, a), (-h, -a)),
        arc((-h, -a), (-a, -h), (-a, -a)),
        line((-a, -h), (a, -h)),
        arc((a, -h), (h, -a), (a, -a)),
    ]


# --- 01 Plate: rounded-square extrusion ---------------------------------------
feature("EXTRUDE",
        {"base": [0, 0, 0], "axis": [0, 0, 1], "xdir": [1, 0, 0], "height": 8.0,
         "profile": rounded_square(30.0, 8.0)},
        "01_Plate_EXTRUDE_add")

# --- 02 Hub -------------------------------------------------------------------
feature("CYLINDER", {"base": [0, 0, 19.0], "axis": [0, 0, 1],
                     "radius": 14.0, "height": 22.0}, "02_Hub_CYL_add")

# --- 03 Bore (through) --------------------------------------------------------
feature("CYLINDER", {"base": [0, 0, 15.0], "axis": [0, 0, 1],
                     "radius": 8.0, "height": 44.0},
        "03_Bore_CYL_sub", operation="SUBTRACT")

# --- 04 Keyway ----------------------------------------------------------------
feature("BOX", {"center": [9.5, 0, 19.0], "ax": [1, 0, 0], "ay": [0, 1, 0],
                "az": [0, 0, 1], "hx": 1.5, "hy": 2.5, "hz": 13.0},
        "04_Keyway_BOX_sub", operation="SUBTRACT")

# --- 05 Relief groove: a turned undercut ring at the hub base -----------------
def ring_profile(r0, r1, z0, z1):
    L = 0.0
    return [
        [L, r0, z0, r1, z0, 0, 0, 0],
        [L, r1, z0, r1, z1, 0, 0, 0],
        [L, r1, z1, r0, z1, 0, 0, 0],
        [L, r0, z1, r0, z0, 0, 0, 0],
    ]
feature("REVOLVE", {"base": [0, 0, 0], "axis": [0, 0, 1],
                    "profile": ring_profile(13.0, 15.0, 7.0, 9.0)},
        "05_Relief_REV_sub", operation="SUBTRACT")

# --- 06 Bolt holes: a counterbored corner pattern -----------------------------
for i, (dx, dy) in enumerate([(-1, -1), (1, -1), (1, 1), (-1, 1)]):
    feature("CYLINDER", {"base": [dx * 20.0, dy * 20.0, 2.0], "axis": [0, 0, 1],
                         "radius": 3.0, "height": 12.0},
            f"06_Bolt{i + 1}_CYL_sub", operation="SUBTRACT",
            extra={"hole_preset": "COUNTERBORE", "cbore_radius": 5.0,
                   "cbore_depth": 3.0})


# --- exploded "how it's made" strip (display-only mesh copies) ----------------
label("HOW IT'S MADE — each of these is one feature; Export merges them (Add ∪, Subtract −)",
      (0, -55, 34), EXPLODE, size=2.0)
for i, (name, obj) in enumerate(_features):
    copy = bpy.data.objects.new(name + "_x", obj.data.copy())
    copy.matrix_world = obj.matrix_world.copy()
    copy.location = (i * 16.0 - (len(_features) - 1) * 8.0, -55.0, 12.0)
    copy.color = obj.color
    copy.show_name = True
    EXPLODE.objects.link(copy)
    role = "SUBTRACT" if obj["reverse"].get("op") == "SUBTRACT" else "ADD"
    label(f"{name.split('_')[1]}\n({role})",
          (i * 16.0 - (len(_features) - 1) * 8.0, -55.0, -4.0), EXPLODE, size=1.4)

label("FLANGE COUPLING — one watertight STEP from 9 features", (0, 30, 40),
      COLL, size=2.6)
scene.reverse.active_feature = 0

# --- write the final STEP via OCCT (merge Add ∪, cut Subtract) -----------------
report_txt = "OCCT kernel not found — install it, then Export STEP to cut the holes."
if occ_export.is_available():
    from reverse_mesh.operators import _feature_from_object, _format_report
    feats = []
    for name, obj in _features:              # already in build order (adds→subs)
        f = _feature_from_object(obj, 1.0)
        if f is not None:
            feats.append(f)
    step_out = os.path.join(REPO, "examples", "flange_coupling.step")
    info = occ_export.export(feats, step_out, unit="MM", merge=True, ordered=True,
                             overshoot=0.05)
    report_txt = _format_report(info)
    print(f"[coupling] STEP  -> {step_out}")
    print(f"[coupling] export: {info}")
    print(f"[coupling] report:\n{report_txt}")
scene.reverse.last_report = report_txt

blend_out = os.path.join(REPO, "examples", "BlenderMesh2STEP_coupling.blend")
bpy.ops.wm.save_as_mainfile(filepath=blend_out)
print(f"[coupling] BLEND -> {blend_out}")
print(f"[coupling] {len(_features)} features "
      f"({sum(1 for _n, o in _features if o['reverse'].get('op') != 'SUBTRACT')} Add / "
      f"{sum(1 for _n, o in _features if o['reverse'].get('op') == 'SUBTRACT')} Subtract)")
print("COUPLING BUILD OK")
