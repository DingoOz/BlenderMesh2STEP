# SPDX-License-Identifier: GPL-3.0-or-later
"""Generate examples/BlenderMesh2STEP_showcase.blend — a capability showcase.

Run headless from the repo root:
    blender --background --python examples/build_showcase.py

Builds a labelled scene in three collections:
  1. Forward-built primitives — parametric, STEP-exportable *by construction*
     (select all → Export STEP works immediately).
  2. Reverse practice — raw meshes to select-faces-and-fit yourself.
  3. Hero: bolted flange — a plate with a boss and drilled/counterbored holes,
     demonstrating Add/Subtract booleans (needs the OCCT kernel to cut).
"""

import math
import os
import sys

import bpy

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import reverse_mesh  # noqa: E402
from reverse_mesh import build, forward  # noqa: E402

reverse_mesh.register()


# --- scene reset + units -------------------------------------------------------
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
# 1 Blender unit = 1 mm, displayed and exported consistently.
scene.unit_settings.system = "METRIC"
scene.unit_settings.length_unit = "MILLIMETERS"
scene.unit_settings.scale_length = 0.001


def new_collection(name):
    coll = bpy.data.collections.new(name)
    scene.collection.children.link(coll)
    return coll


COLL_FWD = new_collection("1 — Forward-built (export-ready)")
COLL_REV = new_collection("2 — Reverse practice (fit these)")
COLL_HERO = new_collection("3 — Hero: bolted flange (booleans)")


def move_to(obj, coll):
    for c in list(obj.users_collection):
        c.objects.unlink(obj)
    coll.objects.link(obj)


def label(text, loc, coll, size=1.4):
    cu = bpy.data.curves.new(text, type="FONT")
    cu.body = text
    cu.size = size
    cu.align_x = "CENTER"
    ob = bpy.data.objects.new(f"txt: {text}", cu)
    ob.location = loc
    ob.rotation_euler = (math.radians(90), 0, 0)   # stand upright, face front
    coll.objects.link(ob)
    return ob


def add_feature(obj, kind, summary, operation="ADD", cut="THROUGH", group="BUILD"):
    """Mirror an object into the scene's Fitted-Features stack (UI convenience)."""
    item = scene.reverse.features.add()
    item.kind = kind
    item.summary = summary
    item.object_name = obj.name
    item.operation = operation
    item.cut_mode = cut
    item.group = group
    return item


def build_primitive(kind, dims, location, name, operation="ADD", cut="THROUGH",
                    coll=COLL_FWD, extra=None):
    """Forward-build a parametric STEP primitive and register it in the stack."""
    params = forward.make_params(kind, dims, location)
    result = forward.make_result(kind, params)
    obj = build.build_object(bpy.context, result, segments=64,
                             operation=operation, cut_mode=cut)
    data = {k: obj["reverse"][k] for k in obj["reverse"].keys()}
    data["group"] = "BUILD"
    if extra:                       # e.g. counterbore preset metadata
        data.update(extra)
    obj["reverse"] = data
    obj.name = name
    obj.data.name = name
    obj.show_name = True
    move_to(obj, coll)
    add_feature(obj, kind, result.summary, operation=operation, cut=cut)
    return obj


# =============================================================================
# Group 1 — Forward-built parametric primitives (export-ready by construction)
# =============================================================================
GAP = 6.0
fwd = [
    ("BOX",     {"hx": 1.5, "hy": 1.0, "hz": 0.8}, "Build_Box"),
    ("CYLINDER", {"radius": 1.2, "height": 2.4}, "Build_Cylinder"),
    ("CONE",    {"radius1": 1.4, "radius2": 0.4, "height": 2.4}, "Build_Cone"),
    ("SPHERE",  {"radius": 1.3}, "Build_Sphere"),
    ("TORUS",   {"major_radius": 1.3, "minor_radius": 0.45}, "Build_Torus"),
    ("EXTRUDE", {"radius": 1.3, "height": 2.0, "sides": 6}, "Build_HexPrism"),
    ("REVOLVE", {"radius1": 0.7, "radius2": 1.4, "height": 0.8}, "Build_Ring"),
]
label("1 · FORWARD-BUILT — select all, Export STEP (works now)",
      (GAP * (len(fwd) - 1) / 2, 0.0, 5.0), COLL_FWD, size=1.0)
for i, (kind, dims, name) in enumerate(fwd):
    build_primitive(kind, dims, (i * GAP, 0.0, 1.5), name, coll=COLL_FWD)
    label(name.split("_")[1], (i * GAP, 0.0, -1.2), COLL_FWD, size=0.7)


# =============================================================================
# Group 2 — Raw meshes to reverse-fit (the user selects faces and clicks Fit)
# =============================================================================
REV_Y = -12.0
label("2 · REVERSE PRACTICE — Edit Mode, select faces, Fit Primitive to Selection",
      (GAP * 3, REV_Y, 5.0), COLL_REV, size=1.0)


def practice(mesh_op_or_verts, x, name, hint, **kw):
    if callable(mesh_op_or_verts):
        mesh_op_or_verts(location=(x, REV_Y, 1.3), **kw)
        obj = bpy.context.active_object
    else:
        verts, faces, matrix = mesh_op_or_verts
        me = bpy.data.meshes.new(name)
        me.from_pydata([tuple(v) for v in verts], [], faces)
        me.update()
        obj = bpy.data.objects.new(name, me)
        obj.matrix_world = matrix
        obj.location = (x, REV_Y, 1.3)
        bpy.context.collection.objects.link(obj)
    obj.name = name
    obj.show_name = True
    move_to(obj, COLL_REV)
    label(hint, (x, REV_Y, -1.2), COLL_REV, size=0.6)
    return obj


# A stepped shaft (multi-diameter) → the clearest "fit REVOLVE" candidate.
shaft_profile = [
    [0.0, 0.0, 0.0, 1.2, 0.0, 0, 0, 0],     # bottom face (radius out)
    [0.0, 1.2, 0.0, 1.2, 1.0, 0, 0, 0],     # large-Ø wall
    [0.0, 1.2, 1.0, 0.6, 1.0, 0, 0, 0],     # shoulder
    [0.0, 0.6, 1.0, 0.6, 2.2, 0, 0, 0],     # small-Ø wall
    [0.0, 0.6, 2.2, 0.0, 2.2, 0, 0, 0],     # top face
    [0.0, 0.0, 2.2, 0.0, 0.0, 0, 0, 0],     # closes along the axis
]
shaft_mesh = build.generate_mesh(
    "REVOLVE", {"axis": [0, 0, 1], "base": [0, 0, 0], "profile": shaft_profile}, 48)

practice(bpy.ops.mesh.primitive_cylinder_add, 0 * GAP, "Fit_Cylinder",
         "→ Cylinder", radius=1.2, depth=2.4, vertices=64)
practice(bpy.ops.mesh.primitive_cube_add, 1 * GAP, "Fit_Box", "→ Box (rotate it!)",
         size=2.2)
practice(bpy.ops.mesh.primitive_cone_add, 2 * GAP, "Fit_Cone", "→ Cone",
         radius1=1.4, radius2=0.3, depth=2.4, vertices=64)
practice(bpy.ops.mesh.primitive_uv_sphere_add, 3 * GAP, "Fit_Sphere", "→ Sphere",
         radius=1.3, segments=48, ring_count=24)
practice(bpy.ops.mesh.primitive_torus_add, 4 * GAP, "Fit_Torus", "→ Torus",
         major_radius=1.3, minor_radius=0.45, major_segments=64, minor_segments=24)
practice(bpy.ops.mesh.primitive_cylinder_add, 5 * GAP, "Fit_HexPrism",
         "→ Extrude (Auto)", radius=1.3, depth=2.0, vertices=6)
practice(shaft_mesh, 6 * GAP, "Fit_SteppedShaft", "→ Revolve (pick it)")

# Give the cube a rotation so 'Box recovers rotation' is visible.
bpy.data.objects["Fit_Box"].rotation_euler = (0.3, 0.4, 0.6)


# =============================================================================
# Group 3 — Hero part: a bolted flange (Add plate + boss, Subtract holes)
# =============================================================================
HERO = (GAP * 3, 12.0, 0.0)
label("3 · HERO — a bolted flange: plate + boss (Add), holes (Subtract).",
      (HERO[0], HERO[1], 6.0), COLL_HERO, size=1.0)
label("Install OCCT, tick 'Merge into one solid', Export → drilled solid + report.",
      (HERO[0], HERO[1], 4.6), COLL_HERO, size=0.7)

hx, hy = 5.0, 3.0
cx, cy, cz = HERO
build_primitive("BOX", {"hx": hx, "hy": hy, "hz": 0.5}, (cx, cy, 0.0),
                "Flange_Plate", coll=COLL_HERO)
build_primitive("CYLINDER", {"radius": 1.6, "height": 1.2}, (cx, cy, 0.35),
                "Flange_Boss", coll=COLL_HERO)
# Central counterbored bore through the boss + plate.
build_primitive("CYLINDER", {"radius": 0.8, "height": 4.0}, (cx, cy, 0.0),
                "Flange_CentreBore", operation="SUBTRACT", coll=COLL_HERO,
                extra={"hole_preset": "COUNTERBORE", "cbore_radius": 1.2,
                       "cbore_depth": 0.5})
# Four corner bolt holes.
for i, (dx, dy) in enumerate([(-1, -1), (1, -1), (1, 1), (-1, 1)]):
    build_primitive("CYLINDER", {"radius": 0.4, "height": 3.0},
                    (cx + dx * (hx - 1.0), cy + dy * (hy - 1.0), 0.0),
                    f"Flange_Bolt{i + 1}", operation="SUBTRACT", coll=COLL_HERO)

# Colour the cutters red so Add/Subtract reads at a glance (Object colour shading).
for o in COLL_HERO.objects:
    d = o.get("reverse")
    if d and d.get("op") == "SUBTRACT":
        o.color = (0.85, 0.25, 0.25, 1.0)


# --- title, view, save ---------------------------------------------------------
label("BlenderMesh2STEP — capability showcase", (GAP * 3, 0.0, 8.0),
      COLL_FWD, size=1.8)

scene.reverse.active_feature = 0

out = os.path.join(REPO, "examples", "BlenderMesh2STEP_showcase.blend")
bpy.ops.wm.save_as_mainfile(filepath=out)

n_obj = sum(1 for o in scene.objects if not o.name.startswith("txt:"))
n_feat = len(scene.reverse.features)
print(f"[showcase] saved {out}")
print(f"[showcase] {n_obj} example objects, {n_feat} features in the stack")
print("SHOWCASE BUILD OK")
