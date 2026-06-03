# SPDX-License-Identifier: GPL-3.0-or-later
"""Operators: extract the selected region, fit a primitive, optionally build it."""

import datetime
import math
import os
import subprocess
import sys

import bmesh
import bpy
import numpy as np
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, EnumProperty, FloatProperty, StringProperty
from bpy.types import Operator
from bpy_extras.io_utils import ExportHelper
from mathutils import Matrix, Vector

from . import build, occ_export, step_export
from .fitting import FITTERS, Region, fit_auto, snap_result, summarize


def occt_lib_dir(create=False):
    """Writable dir where the optional OCCT binding is installed (--target).

    Returns None outside a real extension context (e.g. when the package is
    imported directly in tests), where ``extension_path_user`` is unavailable.
    """
    try:
        return bpy.utils.extension_path_user(__package__, path="occt_lib", create=create)
    except Exception:
        return None


def ensure_occt_on_path():
    """Put the installed OCCT lib dir on sys.path so the binding can import."""
    d = occt_lib_dir(create=False)
    if d and os.path.isdir(d) and d not in sys.path:
        sys.path.insert(0, d)


def _region_from_faces(faces, mw, nmat):
    """Build a world-space :class:`Region` from a list of bmesh faces."""
    verts = {v for f in faces for v in f.verts}
    points = np.array([tuple(mw @ v.co) for v in verts], dtype=float)
    face_points = np.array([tuple(mw @ f.calc_center_median()) for f in faces], dtype=float)
    face_normals = np.array(
        [tuple((nmat @ f.normal).normalized()) for f in faces], dtype=float
    )
    return Region(points=points, face_points=face_points, face_normals=face_normals)


def _selected_faces(obj):
    return [f for f in bmesh.from_edit_mesh(obj.data).faces if f.select]


def _grow_region(seed, pool, angle_threshold_rad, visited=None):
    """Flood-fill a smooth-connected region of faces outward from ``seed``.

    Crosses an edge only to a face that is in ``pool`` and whose normal is within
    ``angle_threshold_rad`` of the current face's. ``visited`` (a set) is updated
    in place, so a caller can segment a whole pool by reusing it across seeds.
    """
    if visited is None:
        visited = set()
    region = [seed]
    visited.add(seed)
    stack = [seed]
    while stack:
        f = stack.pop()
        for edge in f.edges:
            for nf in edge.link_faces:
                if nf in pool and nf not in visited:
                    if f.normal.angle(nf.normal, math.pi) <= angle_threshold_rad:
                        visited.add(nf)
                        region.append(nf)
                        stack.append(nf)
    return region


def _segment_faces(faces, angle_threshold_rad):
    """Group selected faces into smooth-connected regions.

    Two edge-adjacent faces join the same region when the angle between their
    normals is within ``angle_threshold_rad``. Sharp creases (a cube's 90° edges)
    split regions; gently-curving faces (a cylinder's side) stay together.
    """
    selected = set(faces)
    visited = set()
    regions = []
    for seed in faces:
        if seed in visited:
            continue
        regions.append(_grow_region(seed, selected, angle_threshold_rad, visited))
    return regions


class REVERSE_OT_fit_selection(Operator):
    """Fit an analytic primitive to the selected mesh faces"""

    bl_idname = "reverse.fit_selection"
    bl_label = "Fit Primitive to Selection"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == "MESH" and obj.mode == "EDIT"

    @staticmethod
    def _format_candidates(cands):
        """Compact 'winner-first' summary of AUTO candidates, e.g.
        'CYLINDER 0% | SPHERE 0.4% | CONE 1.1%'."""
        return " | ".join(f"{c['kind']} {c['rel_rms'] * 100:.2g}%" for c in cands[:4])

    def _fit_region(self, region, settings):
        kind = settings.primitive_type
        runner_up = ""
        if kind == "AUTO":
            result, cands = fit_auto(region, return_candidates=True)
            runner_up = self._format_candidates(cands)
        else:
            result = FITTERS[kind](region)
        if result is not None and settings.snap_enabled:
            step = (settings.snap_step if settings.snap_preset == "CUSTOM"
                    else float(settings.snap_preset))
            snap_result(result, step=step)   # conservative snap tolerance (own default)
        return result, runner_up

    def _record(self, context, settings, result, runner_up, obj, build_objects,
                source_faces=""):
        """Build the clean object (optional) and append a feature entry."""
        op = settings.default_operation
        cut = settings.default_cut_mode
        obj_name = ""
        if build_objects:
            new_obj = build.build_object(context, result, settings.segments,
                                         operation=op, cut_mode=cut)
            obj_name = new_obj.name
        item = settings.features.add()
        item.kind = result.kind
        item.summary = result.summary
        item.rms = result.rms
        item.max_error = result.max_error
        item.object_name = obj_name
        item.operation = op
        item.cut_mode = cut
        item.runner_up = runner_up
        item.source_object = obj.name
        item.source_faces = source_faces
        settings.active_feature = len(settings.features) - 1

    def execute(self, context):
        settings = context.scene.reverse
        obj = context.active_object

        faces = _selected_faces(obj)
        if not faces:
            self.report({"WARNING"}, "Select at least one face in Edit Mode")
            return {"CANCELLED"}

        mw = obj.matrix_world
        nmat = mw.to_3x3().inverted_safe().transposed()

        if settings.segment_regions:
            clusters = _segment_faces(faces, math.radians(settings.segment_angle))
        else:
            clusters = [faces]

        # Capture geometry AND the source face indices now, while the edit-mode
        # bmesh is still valid (we leave Edit Mode below to build clean objects).
        regions = [_region_from_faces(c, mw, nmat) for c in clusters]
        cluster_faces = [",".join(str(f.index) for f in c) for c in clusters]

        # In single-fit mode, warn if the selection clearly spans several surfaces.
        if not settings.segment_regions:
            n_parts = len(_segment_faces(faces, math.radians(settings.segment_angle)))
            if n_parts > 1:
                self.report(
                    {"WARNING"},
                    f"Selection spans ~{n_parts} surfaces — enable 'Segment regions' "
                    "to fit each separately (e.g. a cube → 6 planes).",
                )

        # Build all clean objects in Object Mode so we don't disturb the edit bmesh.
        prev_mode = obj.mode
        if settings.create_object:
            bpy.ops.object.mode_set(mode="OBJECT")

        results = []
        for region, src_faces in zip(regions, cluster_faces):
            if len(region.points) < 3:
                continue
            result, runner_up = self._fit_region(region, settings)
            if result is not None:
                self._record(context, settings, result, runner_up, obj,
                             settings.create_object, source_faces=src_faces)
                results.append(result)

        if settings.create_object:
            bpy.ops.object.mode_set(mode=prev_mode)

        if not results:
            self.report({"WARNING"}, "Could not fit any primitive to the selection")
            return {"CANCELLED"}

        if len(results) == 1:
            r = results[0]
            level = {"WARNING"} if r.rel_rms > settings.tolerance else {"INFO"}
            self.report(level, f"{r.summary}  ·  RMS {r.rms:.4g} ({r.rel_rms * 100:.2f}%)")
        else:
            kinds = {}
            for r in results:
                kinds[r.kind] = kinds.get(r.kind, 0) + 1
            summary = ", ".join(f"{n}× {k.lower()}" for k, n in sorted(kinds.items()))
            self.report({"INFO"}, f"Fitted {len(results)} regions: {summary}")
        return {"FINISHED"}


class REVERSE_OT_select_similar(Operator):
    """Grow the selection to the whole surface around the active face

    Flood-fills from the active (or first selected) face across the whole mesh,
    following gently-curving neighbours and stopping at sharp creases — so one
    click on a cylinder wall selects the entire wall, but not its end caps.
    """

    bl_idname = "reverse.select_similar"
    bl_label = "Select Similar Surface"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == "MESH" and obj.mode == "EDIT"

    def execute(self, context):
        settings = context.scene.reverse
        obj = context.active_object
        bm = bmesh.from_edit_mesh(obj.data)
        bm.faces.ensure_lookup_table()

        seed = bm.faces.active
        if seed is None or not seed.select:
            seed = next((f for f in bm.faces if f.select), None)
        if seed is None:
            self.report({"WARNING"}, "Select a face to grow the surface from")
            return {"CANCELLED"}

        pool = set(bm.faces)
        region = _grow_region(seed, pool, math.radians(settings.select_similar_angle))
        for f in region:
            f.select_set(True)        # also selects the face's own verts/edges
        # NB: don't select_flush(True) — flushing selection *up* would re-select a
        # cap whose every vertex is shared with the (now-selected) wall quads.
        bmesh.update_edit_mesh(obj.data)
        self.report({"INFO"}, f"Selected {len(region)} faces in the surface")
        return {"FINISHED"}


class REVERSE_OT_clear_features(Operator):
    """Clear the session feature list (does not delete created objects)"""

    bl_idname = "reverse.clear_features"
    bl_label = "Clear Feature List"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        context.scene.reverse.features.clear()
        context.scene.reverse.active_feature = 0
        return {"FINISHED"}


class REVERSE_OT_move_feature(Operator):
    """Move the active feature up or down in the list"""

    bl_idname = "reverse.move_feature"
    bl_label = "Move Feature"
    bl_options = {"REGISTER", "UNDO"}

    direction: EnumProperty(
        items=[("UP", "Up", ""), ("DOWN", "Down", "")], default="UP",
    )

    @classmethod
    def poll(cls, context):
        s = context.scene.reverse
        return len(s.features) > 1 and 0 <= s.active_feature < len(s.features)

    def execute(self, context):
        s = context.scene.reverse
        i = s.active_feature
        j = i - 1 if self.direction == "UP" else i + 1
        if not (0 <= j < len(s.features)):
            return {"CANCELLED"}
        s.features.move(i, j)
        s.active_feature = j
        return {"FINISHED"}


class REVERSE_OT_remove_feature(Operator):
    """Remove the active feature from the list and delete its clean object"""

    bl_idname = "reverse.remove_feature"
    bl_label = "Remove Feature"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        s = context.scene.reverse
        return 0 <= s.active_feature < len(s.features)

    def execute(self, context):
        s = context.scene.reverse
        i = s.active_feature
        name = s.features[i].object_name
        obj = bpy.data.objects.get(name) if name else None
        if obj is not None:
            bpy.data.objects.remove(obj, do_unlink=True)
        s.features.remove(i)
        s.active_feature = min(i, len(s.features) - 1)
        return {"FINISHED"}


class REVERSE_OT_refit_feature(Operator):
    """Re-fit the active feature from its original faces (rebuilds its object)

    Best-effort: re-selects the stored source faces on the source mesh and runs
    the same primitive fit again, so a feature can be regenerated after the fit
    settings change. Brittle if the source mesh topology has since changed.
    """

    bl_idname = "reverse.refit_feature"
    bl_label = "Re-fit Feature"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        s = context.scene.reverse
        if not (0 <= s.active_feature < len(s.features)):
            return False
        f = s.features[s.active_feature]
        return bool(f.source_object and f.source_faces
                    and bpy.data.objects.get(f.source_object))

    def execute(self, context):
        s = context.scene.reverse
        feat = s.features[s.active_feature]
        src = bpy.data.objects.get(feat.source_object)
        if src is None or src.type != "MESH":
            self.report({"WARNING"}, "Source mesh is gone — cannot re-fit")
            return {"CANCELLED"}
        try:
            indices = [int(i) for i in feat.source_faces.split(",") if i != ""]
        except ValueError:
            self.report({"WARNING"}, "Stored source faces are unreadable")
            return {"CANCELLED"}

        if context.object and context.object.mode == "EDIT":
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="DESELECT")
        src.select_set(True)
        context.view_layer.objects.active = src
        bpy.ops.object.mode_set(mode="EDIT")
        bm = bmesh.from_edit_mesh(src.data)
        bm.faces.ensure_lookup_table()
        faces = [bm.faces[i] for i in indices if 0 <= i < len(bm.faces)]
        if not faces:
            bpy.ops.object.mode_set(mode="OBJECT")
            self.report({"WARNING"}, "None of the source faces still exist")
            return {"CANCELLED"}

        mw = src.matrix_world
        nmat = mw.to_3x3().inverted_safe().transposed()
        region = _region_from_faces(faces, mw, nmat)
        bpy.ops.object.mode_set(mode="OBJECT")

        kind = feat.kind
        result = fit_auto(region) if kind == "AUTO" else FITTERS[kind](region)
        if result is None:
            self.report({"WARNING"}, "Re-fit produced no primitive")
            return {"CANCELLED"}
        if s.snap_enabled:
            step = s.snap_step if s.snap_preset == "CUSTOM" else float(s.snap_preset)
            snap_result(result, step=step)

        old = bpy.data.objects.get(feat.object_name) if feat.object_name else None
        new_obj = build.build_object(context, result, s.segments,
                                     operation=feat.operation, cut_mode=feat.cut_mode)
        if old is not None:
            bpy.data.objects.remove(old, do_unlink=True)
        feat.object_name = new_obj.name
        feat.kind = result.kind
        feat.summary = result.summary
        feat.rms = result.rms
        feat.max_error = result.max_error
        self.report({"INFO"}, f"Re-fitted {result.kind} · RMS {result.rms:.4g}")
        return {"FINISHED"}


class REVERSE_OT_set_operation(Operator):
    """Set the boolean role (Add / Subtract) of the active fitted feature"""

    bl_idname = "reverse.set_operation"
    bl_label = "Set Feature Role"
    bl_options = {"REGISTER", "UNDO"}

    operation: EnumProperty(
        items=[("ADD", "Add", "Material / base body"),
               ("SUBTRACT", "Subtract", "A cutter to subtract (e.g. a hole)")],
        default="ADD",
    )

    @classmethod
    def poll(cls, context):
        s = context.scene.reverse
        return 0 <= s.active_feature < len(s.features)

    def execute(self, context):
        s = context.scene.reverse
        item = s.features[s.active_feature]
        item.operation = self.operation
        obj = bpy.data.objects.get(item.object_name) if item.object_name else None
        if obj is not None and "reverse" in obj:
            obj["reverse"]["op"] = self.operation
            obj.color = ((0.85, 0.25, 0.25, 1.0) if self.operation == "SUBTRACT"
                         else (0.8, 0.8, 0.8, 1.0))
        return {"FINISHED"}


class REVERSE_OT_set_cut_mode(Operator):
    """Set the cut mode (Through / Blind) of the active subtractive feature"""

    bl_idname = "reverse.set_cut_mode"
    bl_label = "Set Cut Mode"
    bl_options = {"REGISTER", "UNDO"}

    cut_mode: EnumProperty(
        items=[("THROUGH", "Through", "Cut through both ends"),
               ("BLIND", "Blind", "Keep pocket depth; open only one end")],
        default="THROUGH",
    )

    @classmethod
    def poll(cls, context):
        s = context.scene.reverse
        return 0 <= s.active_feature < len(s.features)

    def execute(self, context):
        s = context.scene.reverse
        item = s.features[s.active_feature]
        item.cut_mode = self.cut_mode
        obj = bpy.data.objects.get(item.object_name) if item.object_name else None
        if obj is not None and "reverse" in obj:
            obj["reverse"]["cut"] = self.cut_mode
        return {"FINISHED"}


class REVERSE_OT_select_feature_object(Operator):
    """Select the clean object created for the active feature"""

    bl_idname = "reverse.select_feature_object"
    bl_label = "Select Feature Object"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        s = context.scene.reverse
        return 0 <= s.active_feature < len(s.features)

    def execute(self, context):
        s = context.scene.reverse
        name = s.features[s.active_feature].object_name
        target = bpy.data.objects.get(name) if name else None
        if target is None:
            self.report({"WARNING"}, "No object linked to this feature")
            return {"CANCELLED"}
        if context.object and context.object.mode == "EDIT":
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="DESELECT")
        target.select_set(True)
        context.view_layer.objects.active = target
        return {"FINISHED"}


# String/bool metadata params that round-trip verbatim from obj["reverse"] into
# the export feature dict (set by features that tag geometry — fillet role,
# thread spec, counterbore preset). Numeric feature params instead extend the
# points/dirs/lengths schema below so they get transformed/scaled correctly.
_METADATA_KEYS = ("role", "thread_spec", "hole_preset")


# Which params are points / directions / lengths, for transforming to world.
_PARAM_KINDS = {
    "PLANE": {"points": ["point"], "dirs": ["normal", "e1", "e2"],
              "lengths": ["half_u", "half_v"]},
    "BOX": {"points": ["center"], "dirs": ["ax", "ay", "az"],
            "lengths": ["hx", "hy", "hz"]},
    "CYLINDER": {"points": ["base"], "dirs": ["axis"], "lengths": ["radius", "height"]},
    "CONE": {"points": ["base", "apex"], "dirs": ["axis"],
             "lengths": ["radius1", "radius2", "height"]},
    "SPHERE": {"points": ["center"], "dirs": [], "lengths": ["radius"]},
    "TORUS": {"points": ["center"], "dirs": ["axis"],
              "lengths": ["major_radius", "minor_radius"]},
}


def _feature_from_object(obj, user_scale):
    """Read an object's stored fit params and return an export feature dict.

    Applies any transform the object has received since it was created (so moving
    the clean object is honoured), then the user's unit scale.
    """
    data = obj.get("reverse")
    if data is None:
        return None
    kind = data["kind"]
    schema = _PARAM_KINDS.get(kind)
    if schema is None:
        return None

    mw = obj.matrix_world
    xform = data.get("_xform")
    if xform is not None and len(xform) == 16:
        x = list(xform)
        creation = Matrix([x[0:4], x[4:8], x[8:12], x[12:16]])
        try:
            delta = mw @ creation.inverted()
        except ValueError:
            delta = Matrix.Identity(4)
    else:
        delta = Matrix.Identity(4)

    rot = delta.to_3x3()
    obj_scale = sum(delta.to_scale()) / 3.0
    s = user_scale

    params = {}
    for key in schema["points"]:
        if key in data.keys():
            v = delta @ Vector(tuple(data[key]))
            params[key] = (v.x * s, v.y * s, v.z * s)
    for key in schema["dirs"]:
        if key in data.keys():
            d = (rot @ Vector(tuple(data[key]))).normalized()
            params[key] = (d.x, d.y, d.z)
    for key in schema["lengths"]:
        if key in data.keys():
            params[key] = float(data[key]) * obj_scale * s
    if "half_angle" in data.keys():
        params["half_angle"] = float(data["half_angle"])
    for key in _METADATA_KEYS:
        if key in data.keys():
            params[key] = data[key]

    rgb = tuple(obj.color[:3])
    color = rgb if any(abs(c - 1.0) > 1e-4 for c in rgb) else None
    op = data["op"] if "op" in data.keys() else "ADD"
    cut = data["cut"] if "cut" in data.keys() else "THROUGH"
    return {"kind": kind, "name": obj.name, "params": params, "color": color,
            "op": op, "cut": cut}


def _format_report(info):
    """Build the human-readable validation report from an occ_export ExportReport."""
    lines = []
    for s in getattr(info, "solids", []):
        flag = "valid" if s["valid"] else "INVALID"
        lines.append(f"Solid {s['index']}: vol {s['volume']:.4g} · {flag}")
    fe = getattr(info, "free_edges", None)
    if fe:
        lines.append(f"{fe} open edge(s) — NOT watertight")
    elif getattr(info, "watertight", None):
        lines.append("watertight ✓")
    if getattr(info, "valid", None) is False:
        lines.append("overall: INVALID topology")
    return "\n".join(lines) if lines else str(info)


class REVERSE_OT_export_step(Operator, ExportHelper):
    """Export fitted analytic primitives as an AP242 STEP assembly"""

    bl_idname = "reverse.export_step"
    bl_label = "Export STEP (AP242)"
    bl_options = {"REGISTER"}

    filename_ext = ".step"
    filter_glob: StringProperty(default="*.step;*.stp", options={"HIDDEN"})

    backend: EnumProperty(
        name="Backend",
        description="How to write the STEP file",
        items=[
            ("AUTO", "Auto", "Use OCCT if installed, else the pure-Python writer"),
            ("PUREPYTHON", "Pure Python", "Built-in analytic writer (no dependencies)"),
            ("OCCT", "OCCT kernel", "Use OpenCASCADE (enables merging into one solid)"),
        ],
        default="AUTO",
    )
    merge_solids: BoolProperty(
        name="Merge into one solid",
        description="Fuse all fitted solids into a single watertight body (OCCT only)",
        default=False,
    )
    cutter_overshoot: FloatProperty(
        name="Cutter overshoot",
        description=(
            "Extend subtractive cylinders/cones by this fraction at each end so a "
            "hole cuts cleanly through coplanar faces (OCCT only). 0 disables"
        ),
        default=0.05, min=0.0, max=1.0, subtype="FACTOR",
    )
    make_watertight: BoolProperty(
        name="Make watertight",
        description=(
            "Sew all faces together, build a closed solid and heal small gaps "
            "(OCCT only). Reports if the result still has open boundaries"
        ),
        default=False,
    )
    sew_tolerance: FloatProperty(
        name="Sew tolerance",
        description="Max gap between faces that the watertight pass will stitch closed",
        default=0.01, min=0.0, max=100.0, precision=4,
    )
    unit: EnumProperty(
        name="Unit",
        description="Length unit declared in the STEP file",
        items=[("MM", "Millimeters", ""), ("M", "Meters", ""), ("IN", "Inches", "")],
        default="MM",
    )
    scale: FloatProperty(
        name="Scale",
        description="Factor applied to all coordinates (e.g. 1000 to write metres as mm)",
        default=1.0, min=1e-6, max=1e6,
    )
    use_selection: BoolProperty(
        name="Selected only",
        description="Export only selected Reverse objects (otherwise all in the scene)",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        return any("reverse" in o for o in context.scene.objects)

    def execute(self, context):
        sources = context.selected_objects if self.use_selection else context.scene.objects
        features = []
        for o in sources:
            if o.type == "MESH" and "reverse" in o:
                feat = _feature_from_object(o, self.scale)
                if feat is not None:
                    features.append(feat)

        if not features:
            self.report({"WARNING"}, "No fitted (Reverse) objects found to export")
            return {"CANCELLED"}

        name = os.path.splitext(os.path.basename(self.filepath))[0] or "Reverse"

        ensure_occt_on_path()
        use_occt = self.backend == "OCCT" or (self.backend == "AUTO" and occ_export.is_available())
        if self.backend == "OCCT" and not occ_export.is_available():
            self.report({"WARNING"}, "OCCT not installed — use the Install button. Falling back to pure-Python.")
            use_occt = False

        if use_occt:
            try:
                info = occ_export.export(features, self.filepath, unit=self.unit,
                                         merge=self.merge_solids,
                                         overshoot=self.cutter_overshoot,
                                         watertight=self.make_watertight,
                                         sew_tol=self.sew_tolerance)
                context.scene.reverse.last_report = _format_report(info)
                self.report({"INFO"}, f"Exported via OCCT: {info}")
                return {"FINISHED"}
            except Exception as exc:
                self.report({"WARNING"}, f"OCCT export failed ({exc}); using pure-Python")

        text = step_export.build_step(
            features,
            unit=self.unit,
            product_name=name,
            timestamp=datetime.datetime.now().isoformat(timespec="seconds"),
            filename=os.path.basename(self.filepath),
        )
        with open(self.filepath, "w", encoding="ascii", errors="replace") as fp:
            fp.write(text)

        context.scene.reverse.last_report = (
            "Validation (volumes / watertightness) requires the OCCT kernel.\n"
            "Install it from the panel to get a per-solid report.")
        self.report({"INFO"}, f"Exported {len(features)} primitives → {os.path.basename(self.filepath)}")
        return {"FINISHED"}


class REVERSE_OT_install_occt(Operator):
    """Download and install the OpenCASCADE binding (cadquery-ocp) for STEP export.

    Installs into the add-on's user folder (not Blender's read-only files), so it
    survives Blender updates and needs no admin rights. ~100 MB download.
    """

    bl_idname = "reverse.install_occt"
    bl_label = "Install OCCT (cadquery-ocp)"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        ensure_occt_on_path()
        return not occ_export.is_available()

    def execute(self, context):
        target = occt_lib_dir(create=True)
        if not target:
            self.report({"ERROR"}, "Could not resolve the add-on data folder")
            return {"CANCELLED"}
        context.window.cursor_set("WAIT")
        try:
            subprocess.run([sys.executable, "-m", "ensurepip"],
                           capture_output=True, text=True)
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--upgrade",
                 "--target", target, "cadquery-ocp"],
                capture_output=True, text=True,
            )
        except Exception as exc:
            context.window.cursor_set("DEFAULT")
            self.report({"ERROR"}, f"Install failed to start: {exc}")
            return {"CANCELLED"}
        finally:
            context.window.cursor_set("DEFAULT")

        if result.returncode != 0:
            tail = (result.stderr or result.stdout)[-400:]
            self.report({"ERROR"}, f"pip install failed: {tail}")
            return {"CANCELLED"}

        ensure_occt_on_path()
        import importlib
        importlib.invalidate_caches()
        if occ_export.is_available():
            self.report({"INFO"}, f"OCCT installed ({occ_export.backend_name()}) — restart not required")
        else:
            self.report({"WARNING"}, "Installed, but the binding did not import. A Blender restart may be needed.")
        return {"FINISHED"}


def menu_export(self, context):
    self.layout.operator(REVERSE_OT_export_step.bl_idname, text="Reverse STEP (AP242) (.step)")


def _reconcile_scene(scene):
    """Sync a scene's feature list with the objects that carry ``["reverse"]``.

    Drops features whose clean object no longer exists, and adds a feature for
    every Reverse object missing from the list — so the stack survives save/reload
    and picks up objects appended from another file.
    """
    s = getattr(scene, "reverse", None)
    if s is None:
        return
    for i in range(len(s.features) - 1, -1, -1):
        name = s.features[i].object_name
        if name and bpy.data.objects.get(name) is None:
            s.features.remove(i)
    known = {f.object_name for f in s.features if f.object_name}
    for o in scene.objects:
        if o.type != "MESH" or "reverse" not in o or o.name in known:
            continue
        data = o["reverse"]
        item = s.features.add()
        item.kind = data.get("kind", "")
        item.object_name = o.name
        item.operation = data.get("op", "ADD")
        item.cut_mode = data.get("cut", "THROUGH")
        item.rms = float(data.get("rms", 0.0))
        item.max_error = float(data.get("max_error", 0.0))
        try:
            item.summary = summarize(item.kind, {k: data[k] for k in data.keys()})
        except (KeyError, TypeError, ValueError):
            item.summary = item.kind
    s.active_feature = min(s.active_feature, max(0, len(s.features) - 1))


@persistent
def _reconcile_features(_dummy):
    for scene in bpy.data.scenes:
        _reconcile_scene(scene)


classes = (
    REVERSE_OT_fit_selection,
    REVERSE_OT_select_similar,
    REVERSE_OT_move_feature,
    REVERSE_OT_remove_feature,
    REVERSE_OT_refit_feature,
    REVERSE_OT_clear_features,
    REVERSE_OT_set_operation,
    REVERSE_OT_set_cut_mode,
    REVERSE_OT_select_feature_object,
    REVERSE_OT_export_step,
    REVERSE_OT_install_occt,
)


def register():
    ensure_occt_on_path()  # pick up a previously-installed binding
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_export.append(menu_export)
    if _reconcile_features not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_reconcile_features)


def unregister():
    if _reconcile_features in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_reconcile_features)
    bpy.types.TOPBAR_MT_file_export.remove(menu_export)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
