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
from bpy.props import (BoolProperty, EnumProperty, FloatProperty, IntProperty,
                       StringProperty)
from bpy.types import Operator
from bpy_extras.io_utils import ExportHelper
from mathutils import Matrix, Vector

from . import build, forward, occ_export, overlay, pmi_export, step_export, units
from . import properties as props_mod
from .fitting import (
    FITTERS, MeshGraph, Region, classify_arrangement, fit_auto, fit_fillet,
    fit_robust, match_cylinders, optimize_decomposition, signed_distances,
    snap_result, summarize,
)
from .fitting.common import deviation_color
from .fitting.solidfit import SDFGrid, fit_solids


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
    index = {}
    points = []
    face_verts = []
    for f in faces:
        idxs = []
        for v in f.verts:
            k = index.get(v)
            if k is None:
                k = index[v] = len(points)
                points.append(tuple(mw @ v.co))
            idxs.append(k)
        face_verts.append(tuple(idxs))
    points = np.array(points, dtype=float)
    face_points = np.array([tuple(mw @ f.calc_center_median()) for f in faces], dtype=float)
    face_normals = np.array(
        [tuple((nmat @ f.normal).normalized()) for f in faces], dtype=float
    )
    return Region(points=points, face_points=face_points, face_normals=face_normals,
                  face_verts=face_verts)


def _selected_faces(obj):
    return [f for f in bmesh.from_edit_mesh(obj.data).faces if f.select]


def _face_tris_world(face, mw):
    """Fan-triangulate a bmesh face into world-space (x, y, z) vertex triples."""
    vs = [mw @ v.co for v in face.verts]
    return [(tuple(vs[0]), tuple(vs[i]), tuple(vs[i + 1]))
            for i in range(1, len(vs) - 1)]


def _add_feature(context, settings, result, src_obj, *, build_object, operation,
                 cut, runner_up="", source_faces=""):
    """Build the optional clean object and append a feature entry; return the item.

    Shared by the fit operator and pattern propagation (which supplies the seed's
    role/cut instead of the scene defaults)."""
    obj_name = ""
    if build_object:
        new_obj = build.build_object(context, result, settings.segments,
                                     operation=operation, cut_mode=cut)
        obj_name = new_obj.name
    item = settings.features.add()
    item.kind = result.kind
    item.summary = result.summary
    item.rms = result.rms
    item.max_error = result.max_error
    item.object_name = obj_name
    item.operation = operation
    item.cut_mode = cut
    item.runner_up = runner_up
    item.source_object = src_obj.name
    item.source_faces = source_faces
    settings.active_feature = len(settings.features) - 1
    return item


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


AUTO_COLLECTION = "Reverse Auto"
SOLID_COLLECTION = "Reverse Solid"


def _snap_step(settings):
    """Resolve the active snap grid from the settings (preset or custom)."""
    return (settings.snap_step if settings.snap_preset == "CUSTOM"
            else float(settings.snap_preset))


def _parse_angles(text):
    """Parse the comma/semicolon-separated crease-angle sweep string."""
    angles = []
    for tok in str(text).replace(";", ",").split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            a = float(tok)
        except ValueError:
            continue
        if 0.0 < a <= 180.0:
            angles.append(a)
    return tuple(angles) if angles else (40.0, 25.0, 12.0, 6.0)


def _mesh_graph(obj):
    """Snapshot ``obj``'s mesh into a pure :class:`MeshGraph` (world space).

    Mirrors :func:`_region_from_faces`' transforms (``mw`` for points, the
    inverse-transpose for normals) but for the whole mesh, and frees the bmesh so
    nothing downstream holds a live reference (it must survive across modal ticks).
    """
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    bm.verts.index_update()
    bm.faces.index_update()

    mw = obj.matrix_world
    nmat = mw.to_3x3().inverted_safe().transposed()

    verts = np.array([tuple(mw @ v.co) for v in bm.verts], dtype=float)
    face_vert_idx = [np.array([v.index for v in f.verts], dtype=int) for f in bm.faces]
    centroids = np.array([tuple(mw @ f.calc_center_median()) for f in bm.faces], dtype=float)
    normals = np.array(
        [tuple((nmat @ f.normal).normalized()) for f in bm.faces], dtype=float)
    areas = np.array([f.calc_area() for f in bm.faces], dtype=float)
    adjacency = []
    for f in bm.faces:
        nbrs = set()
        for e in f.edges:
            for nf in e.link_faces:
                if nf is not f:
                    nbrs.add(nf.index)
        adjacency.append(sorted(nbrs))

    graph = MeshGraph(verts=verts, face_vert_idx=face_vert_idx, centroids=centroids,
                      normals=normals, areas=areas, adjacency=adjacency)
    bm.free()
    return graph


def _ensure_collection(context, name):
    """Return (creating if needed) a scene collection by ``name``."""
    coll = bpy.data.collections.get(name)
    if coll is None:
        coll = bpy.data.collections.new(name)
    if coll.name not in context.scene.collection.children:
        try:
            context.scene.collection.children.link(coll)
        except RuntimeError:
            pass
    return coll


def _move_to_collection(obj, coll):
    """Unlink ``obj`` from wherever build_object placed it and link it to ``coll``."""
    for c in list(obj.users_collection):
        c.objects.unlink(obj)
    coll.objects.link(obj)


def _build_leftover_object(context, src_obj, face_indices, coll):
    """Copy ``face_indices`` of ``src_obj`` into a 'Reverse_Leftover' mesh object.

    The patch keeps the source's transform; the geometry lives in the object's
    own mesh (nothing heavy in IDProperties) and is tagged kind='MESH_PATCH' so
    the STEP export writes it as faceted planar faces.
    """
    keep = set(int(i) for i in face_indices)
    bm = bmesh.new()
    bm.from_mesh(src_obj.data)
    bm.faces.ensure_lookup_table()
    bmesh.ops.delete(bm, geom=[f for f in bm.faces if f.index not in keep],
                     context="FACES")
    mesh = bpy.data.meshes.new("Reverse_Leftover")
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new("Reverse_Leftover", mesh)
    obj.matrix_world = src_obj.matrix_world.copy()
    coll.objects.link(obj)
    obj["reverse"] = {"kind": "MESH_PATCH", "group": "AUTO",
                      "op": "ADD", "cut": "THROUGH"}
    return obj


def _add_leftover_feature(context, settings, obj, n_faces):
    """Append a feature-stack entry for a leftover mesh patch."""
    item = settings.features.add()
    item.kind = "MESH_PATCH"
    item.group = "AUTO"
    item.summary = f"Leftover · {n_faces} faces"
    item.object_name = obj.name
    item.operation = "ADD"
    item.cut_mode = "THROUGH"
    item.source_object = obj.name
    settings.active_feature = len(settings.features) - 1
    return item


def _add_built_feature(context, settings, result, obj, group, *,
                       operation="ADD", cut_mode="THROUGH"):
    """Append a feature for a machine-built primitive, tagged with ``group``."""
    item = settings.features.add()
    item.kind = result.kind
    item.group = group
    item.summary = result.summary
    item.rms = result.rms
    item.max_error = result.max_error
    item.object_name = obj.name
    item.operation = operation
    item.cut_mode = cut_mode
    item.source_object = obj.name
    settings.active_feature = len(settings.features) - 1
    return item


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
        if kind == "FILLET":
            result = fit_fillet(region)
        elif kind == "AUTO":
            result, cands = fit_auto(region, return_candidates=True)
            runner_up = self._format_candidates(cands)
            if result is not None and settings.use_ransac:
                # Keep AUTO's chosen kind, but refit it robustly to drop outliers.
                result = fit_robust(region, FITTERS[result.kind],
                                    rel_threshold=settings.ransac_threshold) or result
        elif settings.use_ransac:
            result = fit_robust(region, FITTERS[kind],
                                rel_threshold=settings.ransac_threshold)
        else:
            result = FITTERS[kind](region)
        if result is not None and settings.snap_enabled:
            step = (settings.snap_step if settings.snap_preset == "CUSTOM"
                    else float(settings.snap_preset))
            snap_result(result, step=step)   # conservative snap tolerance (own default)
        return result, runner_up

    @staticmethod
    def _push_heatmap(settings, region, result, geo, idx):
        """Colour each selected face by its deviation from the fitted surface."""
        dev = np.abs(signed_distances(result, region.face_points))
        scale = result.params.get("_scale", 1.0) or 1.0
        denom = max(settings.tolerance * scale, 1e-12)   # red at one tolerance off
        coords, colors = [], []
        for face_tris, d in zip(geo, dev):
            col = deviation_color(d / denom)
            for tri in face_tris:
                for v in tri:
                    coords.append(v)
                    colors.append(col)
        if coords:
            overlay.set_tris(f"heatmap:{idx}", coords, colors)

    def _record(self, context, settings, result, runner_up, obj, build_objects,
                source_faces=""):
        """Build the clean object (optional) and append a feature entry."""
        _add_feature(context, settings, result, obj, build_object=build_objects,
                     operation=settings.default_operation, cut=settings.default_cut_mode,
                     runner_up=runner_up, source_faces=source_faces)

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
        # cluster_geo holds each face's world-space fan triangles (for the heatmap),
        # in the same face order as the region's face_points.
        regions = [_region_from_faces(c, mw, nmat) for c in clusters]
        cluster_faces = [",".join(str(f.index) for f in c) for c in clusters]
        cluster_geo = [[_face_tris_world(f, mw) for f in c] for c in clusters]

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

        # Refresh the deviation heatmap: drop any previous overlay first.
        overlay.clear_prefix("heatmap:")

        results = []
        for region, src_faces, geo in zip(regions, cluster_faces, cluster_geo):
            if len(region.points) < 3:
                continue
            result, runner_up = self._fit_region(region, settings)
            if result is not None:
                self._record(context, settings, result, runner_up, obj,
                             settings.create_object, source_faces=src_faces)
                results.append(result)
                if settings.show_heatmap:
                    self._push_heatmap(settings, region, result, geo, len(results) - 1)

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


class REVERSE_OT_propagate_pattern(Operator):
    """Find and fit every hole matching the active one across the whole mesh

    From a seed cylinder (e.g. one hole of a bolt circle), this segments the
    source mesh, fits each region, and recovers every other cylinder with the
    same radius and a parallel axis — fitting them with the seed's role/cut. It
    also reports the arrangement (circular, linear, scattered).
    """

    bl_idname = "reverse.propagate_pattern"
    bl_label = "Propagate Pattern"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        s = context.scene.reverse
        if not (0 <= s.active_feature < len(s.features)):
            return False
        f = s.features[s.active_feature]
        return f.kind == "CYLINDER" and bool(f.source_object) and \
            bpy.data.objects.get(f.source_object) is not None

    def execute(self, context):
        s = context.scene.reverse
        feat = s.features[s.active_feature]
        src = bpy.data.objects.get(feat.source_object)
        seed_obj = bpy.data.objects.get(feat.object_name) if feat.object_name else None
        if src is None or seed_obj is None or "reverse" not in seed_obj:
            self.report({"WARNING"}, "Seed feature's mesh/object is gone")
            return {"CANCELLED"}

        seed = _feature_from_object(seed_obj, 1.0)["params"]
        seed_cyl = {"radius": seed["radius"], "axis": seed["axis"],
                    "center": seed["base"], "height": seed["height"]}
        seed_center = np.array(seed["base"])
        seed_r = seed["radius"]

        # Segment the whole source mesh and fit a cylinder to each region.
        prev_active = context.view_layer.objects.active
        if context.object and context.object.mode == "EDIT":
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="DESELECT")
        src.select_set(True)
        context.view_layer.objects.active = src
        bpy.ops.object.mode_set(mode="EDIT")
        bm = bmesh.from_edit_mesh(src.data)
        all_faces = list(bm.faces)
        clusters = _segment_faces(all_faces, math.radians(s.segment_angle))
        mw = src.matrix_world
        nmat = mw.to_3x3().inverted_safe().transposed()
        regions = [_region_from_faces(c, mw, nmat) for c in clusters]
        cluster_idx = [",".join(str(f.index) for f in c) for c in clusters]
        bpy.ops.object.mode_set(mode="OBJECT")

        candidates = []
        for region in regions:
            if len(region.points) < 6:
                candidates.append(None)
                continue
            fit = FITTERS["CYLINDER"](region)
            candidates.append(None if fit is None else {
                "radius": fit.params["radius"], "axis": fit.params["axis"],
                "center": fit.params["base"], "height": fit.params["height"], "_fit": fit})

        idxs = match_cylinders(seed_cyl, candidates,
                               radius_tol=0.05, axis_tol_deg=5.0)

        matched_centers = []
        created = 0
        for i in idxs:
            c = candidates[i]
            center = np.array(c["center"])
            matched_centers.append(center)
            if np.linalg.norm(center - seed_center) < 0.5 * max(seed_r, 1e-6):
                continue                              # this is the seed itself
            _add_feature(context, s, c["_fit"], src, build_object=True,
                         operation=feat.operation, cut=feat.cut_mode,
                         source_faces=cluster_idx[i])
            created += 1

        if prev_active is not None:
            context.view_layer.objects.active = prev_active

        kind, info = classify_arrangement(matched_centers, seed["axis"]) \
            if matched_centers else ("SINGLE", {})
        if created == 0:
            self.report({"INFO"}, "No other matching holes found")
            return {"FINISHED"}
        self.report({"INFO"},
                    f"Propagated {created} more hole(s) · {kind.lower()} pattern "
                    f"of {len(matched_centers)}")
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


class REVERSE_OT_auto_decompose(Operator):
    """Whole-mesh auto-decomposition (HEAVY / EXPERIMENTAL)

    Point at a mesh and press once — no manual face selection. Segments the ENTIRE
    active mesh at several scales, fits the best analytic primitive to every smooth
    region, then globally optimizes which *set* of primitives best explains the
    part (fewest primitives, lowest residual, most coverage). The result is built as
    a SEPARATE set of clean primitives in the 'Reverse Auto' collection, ready for
    STEP export. Press Esc to cancel mid-build.

    Heavy: it tries a whole pool of competing hypotheses. Very noisy scan meshes are
    better remeshed/cleaned first — this expects reasonably clean geometry.
    """

    bl_idname = "reverse.auto_decompose"
    bl_label = "Auto-Decompose Whole Mesh"
    bl_options = {"REGISTER", "UNDO"}

    _timer = None

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == "MESH"

    @staticmethod
    def _empty_message(context, out):
        """Actionable reason no primitives came back, for the user report."""
        obj = context.active_object
        if obj is None or not len(obj.data.polygons):
            return "Auto-decompose: the active mesh has no faces"
        if out.n_candidates == 0:
            return ("Auto-decompose found no primitives — no region fit a surface "
                    "within tolerance. Raise 'Decompose tolerance', lower 'Min "
                    "faces / region', or the part may be too freeform")
        return ("Auto-decompose found no primitives — candidates were rejected by "
                "the optimizer. Try a higher 'Decompose tolerance' or lower 'Merge "
                "pressure (λ)'")

    def _compute(self, context):
        """Heavy synchronous phase: extract the mesh graph and optimize."""
        s = context.scene.reverse
        obj = context.active_object
        if obj.mode == "EDIT":
            bpy.ops.object.mode_set(mode="OBJECT")
        graph = _mesh_graph(obj)
        self._src = obj
        wm = context.window_manager
        out = optimize_decomposition(
            graph,
            angles=_parse_angles(s.decompose_angles),
            lam=s.decompose_lambda, mu=s.decompose_mu, nu=s.decompose_nu,
            tolerance=s.decompose_tolerance,
            alignment_gate=0.9,
            min_faces=s.decompose_min_faces,
            snap=(_snap_step(s) if s.snap_enabled else None),
            merge=s.decompose_merge,
            progress=lambda frac: wm.progress_update(int(100 * frac)),
        )
        self._out = out
        return out

    def _build_one(self, context, result):
        s = context.scene.reverse
        obj = build.build_object(context, result, s.segments,
                                 operation="ADD", cut_mode="THROUGH")
        _move_to_collection(obj, self._coll)
        try:
            obj["reverse"]["group"] = "AUTO"
        except (KeyError, TypeError):
            pass
        _add_built_feature(context, s, result, obj, "AUTO")

    def _build_leftovers(self, context):
        """Optionally keep the unexplained faces as a MESH_PATCH object."""
        s = context.scene.reverse
        out = getattr(self, "_out", None)
        src = getattr(self, "_src", None)
        if (not s.decompose_keep_leftovers or out is None or src is None
                or not out.leftover_faces):
            return
        obj = _build_leftover_object(context, src, out.leftover_faces, self._coll)
        _add_leftover_feature(context, s, obj, len(out.leftover_faces))

    def invoke(self, context, event):
        wm = context.window_manager
        context.window.cursor_set("WAIT")
        wm.progress_begin(0, 100)
        try:
            out = self._compute(context)
        finally:
            context.window.cursor_set("DEFAULT")
        if not out.results:
            wm.progress_end()
            self.report({"WARNING"}, self._empty_message(context, out))
            return {"CANCELLED"}
        self._results = out.results
        self._i = 0
        self._built = 0
        self._coll = _ensure_collection(context, AUTO_COLLECTION)
        self._timer = wm.event_timer_add(0.01, window=context.window)
        wm.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type == "ESC":
            return self._finish(context, cancelled=True)
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        wm = context.window_manager
        end = min(self._i + 8, len(self._results))          # build 8 objects per tick
        for r in self._results[self._i:end]:
            self._build_one(context, r)
            self._built += 1
        self._i = end
        wm.progress_update(int(100 * self._i / max(len(self._results), 1)))
        if context.area:
            context.area.tag_redraw()
        if self._i >= len(self._results):
            return self._finish(context, cancelled=False)
        return {"RUNNING_MODAL"}

    def _finish(self, context, *, cancelled):
        wm = context.window_manager
        if self._timer is not None:
            wm.event_timer_remove(self._timer)
            self._timer = None
        wm.progress_end()
        if cancelled:
            self.report({"WARNING"}, f"Cancelled — built {self._built} primitives")
            return {"CANCELLED"}
        out = getattr(self, "_out", None)
        cov = out.coverage if out else 0.0
        leftover = len(out.leftover_faces) if out else 0
        self._build_leftovers(context)
        level = ({"WARNING"} if cov < context.scene.reverse.decompose_min_coverage
                 else {"INFO"})
        kept = " (kept as Reverse_Leftover)" if (
            leftover and context.scene.reverse.decompose_keep_leftovers) else ""
        self.report(level, f"Auto-decomposed into {self._built} primitives · "
                           f"{cov * 100:.0f}% area covered ({leftover} faces left over{kept})")
        return {"FINISHED"}

    def execute(self, context):
        """Synchronous fallback — modal handlers don't pump under ``--background``."""
        wm = context.window_manager
        wm.progress_begin(0, 100)
        try:
            out = self._compute(context)
        finally:
            wm.progress_end()
        if not out.results:
            self.report({"WARNING"}, self._empty_message(context, out))
            return {"CANCELLED"}
        self._coll = _ensure_collection(context, AUTO_COLLECTION)
        self._built = 0
        for r in out.results:
            self._build_one(context, r)
            self._built += 1
        self._build_leftovers(context)
        self.report({"INFO"}, f"Auto-decomposed into {self._built} primitives · "
                              f"{out.coverage * 100:.0f}% covered")
        return {"FINISHED"}


class REVERSE_OT_clear_auto_set(Operator):
    """Delete the auto-decomposed primitives (the 'Reverse Auto' collection)"""

    bl_idname = "reverse.clear_auto_set"
    bl_label = "Clear Auto Set"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        coll = bpy.data.collections.get(AUTO_COLLECTION)
        return coll is not None and len(coll.objects) > 0

    def execute(self, context):
        coll = bpy.data.collections.get(AUTO_COLLECTION)
        s = context.scene.reverse
        removed = {o.name for o in list(coll.objects)} if coll else set()
        if coll:
            for o in list(coll.objects):
                bpy.data.objects.remove(o, do_unlink=True)
        # Drop the matching AUTO features.
        for i in range(len(s.features) - 1, -1, -1):
            f = s.features[i]
            if f.group == "AUTO" or f.object_name in removed:
                s.features.remove(i)
        s.active_feature = min(s.active_feature, max(0, len(s.features) - 1))
        self.report({"INFO"}, f"Cleared {len(removed)} auto primitives")
        return {"FINISHED"}


def _non_manifold_edges(obj):
    """Count edges that aren't shared by exactly two faces (watertightness proxy)."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    n = sum(1 for e in bm.edges if len(e.link_faces) != 2)
    bm.free()
    return n


def _signed_distance_grid(obj, resolution, progress=None):
    """Sample a signed-distance field of ``obj``'s solid onto a world-space grid.

    Positive inside, negative outside — the sign comes from the nearest surface
    normal (``closest_point_on_mesh``), so it is only reliable on a watertight mesh
    with consistent normals (the operator warns otherwise). Distances are measured
    in world space so object scale is honoured.
    """
    mw = obj.matrix_world
    mwi = mw.inverted_safe()
    nmat = mw.to_3x3().inverted_safe().transposed()

    corners = np.array([tuple(mw @ Vector(c)) for c in obj.bound_box], dtype=float)
    lo = corners.min(axis=0)
    hi = corners.max(axis=0)
    diag = float(np.linalg.norm(hi - lo)) or 1.0
    pad = 0.03 * diag
    lo -= pad
    hi += pad
    spacing = max((hi - lo).max() / max(resolution, 1), 1e-6)
    ns = (np.ceil((hi - lo) / spacing).astype(int) + 1)
    nx, ny, nz = (int(n) for n in ns)

    xs = lo[0] + np.arange(nx) * spacing
    ys = lo[1] + np.arange(ny) * spacing
    zs = lo[2] + np.arange(nz) * spacing
    sd = np.empty((nx, ny, nz), dtype=float)
    for i in range(nx):
        for j in range(ny):
            for k in range(nz):
                pw = Vector((xs[i], ys[j], zs[k]))
                ok, loc, nrm, _ = obj.closest_point_on_mesh(mwi @ pw)
                if not ok:
                    sd[i, j, k] = -1e18
                    continue
                d = pw - (mw @ loc)
                dist = d.length
                nw = nmat @ nrm
                sd[i, j, k] = -dist if d.dot(nw) > 0.0 else dist
        if progress is not None:
            progress(0.05 + 0.6 * (i + 1) / nx)
    return SDFGrid(sd=sd, origin=lo, spacing=spacing)


class REVERSE_OT_solid_decompose(Operator):
    """Whole-mesh SOLID decomposition (HEAVY / EXPERIMENTAL)

    Approximates the mesh's *volume* as a union of solid primitives (sphere /
    cylinder / box) — additive CSG — instead of fitting surface patches. A capsule
    comes back as cylinder + 2 spheres regardless of tessellation, and nothing juts
    outside the surface (every primitive is inscribed in the volume). The solids are
    built into the 'Reverse Solid' collection; export with the OCCT backend's
    'Merge into one solid' to boolean-union them into one watertight body.

    Needs a watertight mesh with consistent normals to read inside/outside reliably.
    """

    bl_idname = "reverse.solid_decompose"
    bl_label = "Auto-Decompose Solid (Boolean)"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == "MESH"

    def execute(self, context):
        s = context.scene.reverse
        obj = context.active_object
        if obj.mode == "EDIT":
            bpy.ops.object.mode_set(mode="OBJECT")

        nm = _non_manifold_edges(obj)
        wm = context.window_manager
        context.window.cursor_set("WAIT")
        wm.progress_begin(0, 100)
        try:
            grid = _signed_distance_grid(
                obj, s.solid_resolution,
                progress=lambda f: wm.progress_update(int(100 * f)))
            results, coverage = fit_solids(
                grid, max_primitives=s.solid_max_primitives,
                progress=lambda f: wm.progress_update(int(100 * f)))
        finally:
            wm.progress_end()
            context.window.cursor_set("DEFAULT")

        if not results:
            self.report({"WARNING"},
                        "Solid decompose found nothing — is the mesh watertight "
                        "with outward normals? Try a higher Volume resolution")
            return {"CANCELLED"}

        coll = _ensure_collection(context, SOLID_COLLECTION)
        for r in results:
            o = build.build_object(context, r, s.segments,
                                   operation="ADD", cut_mode="THROUGH")
            _move_to_collection(o, coll)
            try:
                o["reverse"]["group"] = "BOOL"
            except (KeyError, TypeError):
                pass
            _add_built_feature(context, s, r, o, "BOOL")

        msg = (f"Solid decompose: {len(results)} primitives, "
               f"{coverage * 100:.0f}% volume covered")
        if nm:
            msg += f" · warning: mesh not watertight ({nm} open edges) — signs may be off"
        level = ({"WARNING"} if coverage < s.solid_min_coverage or nm else {"INFO"})
        self.report(level, msg)
        return {"FINISHED"}


class REVERSE_OT_clear_solid_set(Operator):
    """Delete the solid/boolean primitives (the 'Reverse Solid' collection)"""

    bl_idname = "reverse.clear_solid_set"
    bl_label = "Clear Solid Set"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        coll = bpy.data.collections.get(SOLID_COLLECTION)
        return coll is not None and len(coll.objects) > 0

    def execute(self, context):
        coll = bpy.data.collections.get(SOLID_COLLECTION)
        s = context.scene.reverse
        removed = {o.name for o in list(coll.objects)} if coll else set()
        if coll:
            for o in list(coll.objects):
                bpy.data.objects.remove(o, do_unlink=True)
        for i in range(len(s.features) - 1, -1, -1):
            f = s.features[i]
            if f.group == "BOOL" or f.object_name in removed:
                s.features.remove(i)
        s.active_feature = min(s.active_feature, max(0, len(s.features) - 1))
        self.report({"INFO"}, f"Cleared {len(removed)} solid primitives")
        return {"FINISHED"}


# --- Forward building: STEP primitives from typed dimensions --------------------


class REVERSE_OT_add_primitive(Operator):
    """Add a STEP-exportable primitive solid at the 3D cursor"""

    bl_idname = "reverse.add_primitive"
    bl_label = "Add STEP Primitive"
    bl_options = {"REGISTER", "UNDO"}

    kind: EnumProperty(name="Primitive", items=props_mod.BUILD_PRIMITIVE_ITEMS,
                       default="BOX")
    radius: FloatProperty(name="Radius", min=1e-6, default=1.0, unit="LENGTH")
    height: FloatProperty(name="Height", min=1e-6, default=2.0, unit="LENGTH")
    radius1: FloatProperty(name="Base radius", min=0.0, default=1.0, unit="LENGTH")
    radius2: FloatProperty(name="Top radius", min=0.0, default=0.5, unit="LENGTH")
    major_radius: FloatProperty(name="Major radius", min=1e-6, default=1.0,
                                unit="LENGTH")
    minor_radius: FloatProperty(name="Minor radius", min=1e-6, default=0.25,
                                unit="LENGTH")
    sides: IntProperty(name="Sides", min=3, max=64, default=6,
                       description="Profile side count for an extruded N-gon prism")
    hx: FloatProperty(name="Half X", min=1e-6, default=1.0, unit="LENGTH")
    hy: FloatProperty(name="Half Y", min=1e-6, default=1.0, unit="LENGTH")
    hz: FloatProperty(name="Half Z", min=1e-6, default=1.0, unit="LENGTH")

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT"

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.prop(self, "kind")
        if self.kind == "EXTRUDE":
            layout.prop(self, "sides")
        for key, _label in forward.PARAM_FIELDS[self.kind]:
            layout.prop(self, key)

    def invoke(self, context, event):
        # Default the kind from the Build panel's selector unless set explicitly.
        if not self.properties.is_property_set("kind"):
            self.kind = context.scene.reverse.build_primitive_type
        if not self.properties.is_property_set("sides"):
            self.sides = context.scene.reverse.build_extrude_sides
        return self.execute(context)

    def execute(self, context):
        settings = context.scene.reverse
        dims = {key: getattr(self, key) for key, _label in forward.PARAM_FIELDS[self.kind]}
        if self.kind == "EXTRUDE":
            dims["sides"] = self.sides
        if self.kind == "CONE" and dims["radius1"] <= 0.0 and dims["radius2"] <= 0.0:
            self.report({"WARNING"}, "Cone needs at least one non-zero radius")
            return {"CANCELLED"}
        params = forward.make_params(self.kind, dims, context.scene.cursor.location)
        result = forward.make_result(self.kind, params)
        obj = build.build_object(context, result, segments=settings.segments,
                                 operation=settings.default_operation,
                                 cut_mode=settings.default_cut_mode)
        data = {k: obj["reverse"][k] for k in obj["reverse"].keys()}
        data["group"] = "BUILD"
        obj["reverse"] = data
        obj.name = f"Build_{self.kind.title()}"
        obj.data.name = obj.name
        _add_built_feature(context, settings, result, obj, "BUILD",
                           operation=settings.default_operation,
                           cut_mode=settings.default_cut_mode)
        for o in context.selected_objects:
            o.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        props_mod.sync_build_params(obj)   # make the panel fields live immediately
        self.report({"INFO"}, result.summary)
        return {"FINISHED"}


class REVERSE_OT_edit_params(Operator):
    """Load this primitive's stored dimensions into the editable panel fields"""

    bl_idname = "reverse.edit_params"
    bl_label = "Edit Parameters"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and "reverse" in obj

    def execute(self, context):
        if not props_mod.sync_build_params(context.active_object):
            self.report({"WARNING"}, "Active object has no editable primitive parameters")
            return {"CANCELLED"}
        return {"FINISHED"}


class REVERSE_OT_rebuild_feature(Operator):
    """Regenerate this primitive's mesh from its stored parameters (fixes drift)"""

    bl_idname = "reverse.rebuild_feature"
    bl_label = "Rebuild from Parameters"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (obj is not None and "reverse" in obj and context.mode == "OBJECT"
                and obj["reverse"]["kind"] != "MESH_PATCH")

    def execute(self, context):
        obj = context.active_object
        try:
            forward.rebuild_object(obj, segments=context.scene.reverse.segments)
        except (ValueError, KeyError) as exc:
            self.report({"WARNING"}, f"Cannot rebuild {obj.name}: {exc}")
            return {"CANCELLED"}
        props_mod.sync_build_params(obj)
        self.report({"INFO"}, f"{obj.name} rebuilt from stored parameters")
        return {"FINISHED"}


class REVERSE_OT_bake_scale(Operator):
    """Fold the object's uniform scale into its stored dimensions and reset scale"""

    bl_idname = "reverse.bake_scale"
    bl_label = "Bake Scale into Parameters"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (obj is not None and "reverse" in obj and context.mode == "OBJECT"
                and obj["reverse"]["kind"] in _PARAM_KINDS)

    def execute(self, context):
        obj = context.active_object
        data = obj.get("reverse")
        kind = data["kind"]
        s = obj.scale
        comps = sorted(abs(c) for c in s)
        if comps[0] <= 1e-12:
            self.report({"WARNING"}, "Object has a zero scale component")
            return {"CANCELLED"}
        if comps[2] / comps[0] > 1.001 and kind != "BOX":
            self.report({"WARNING"},
                        "Non-uniform scale on a curved primitive cannot be baked — "
                        "clear the scale instead (Alt+S)")
            return {"CANCELLED"}
        factor = (abs(s.x) + abs(s.y) + abs(s.z)) / 3.0
        new = {k: data[k] for k in data.keys()}
        for key in _PARAM_KINDS[kind]["lengths"]:
            if key in new:
                new[key] = float(new[key]) * factor
        if kind == "EXTRUDE" and "profile" in new:
            new["profile"] = [
                [float(r[0])] + [float(x) * factor for x in list(r)[1:7]] + [float(r[7])]
                for r in new["profile"]
            ]
        obj["reverse"] = new
        obj.scale = (1.0, 1.0, 1.0)
        # matrix_world is lazily evaluated — flush the scale change before the
        # rebuild reads it, or the old scale leaks back in via the xform delta.
        context.view_layer.update()
        forward.rebuild_object(obj, segments=context.scene.reverse.segments)
        props_mod.sync_build_params(obj)
        # Keep the stack summary truthful after the dimension change.
        d = obj["reverse"]
        for f in context.scene.reverse.features:
            if f.object_name == obj.name:
                f.summary = summarize(kind, {k: d[k] for k in d.keys()})
        self.report({"INFO"}, f"Scale ×{factor:.4g} baked into {obj.name}")
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
    "FILLET": {"points": ["base"], "dirs": ["axis", "ref"],
               "lengths": ["radius", "height"]},
    # EXTRUDE's 2D profile coordinates are lengths in the (xdir, axis×xdir)
    # frame — scaled with the object, handled specially in _feature_from_object.
    "EXTRUDE": {"points": ["base"], "dirs": ["axis", "xdir"],
                "lengths": ["height", "radius"]},
}


# Triangle count above which exporting a leftover MESH_PATCH draws a warning.
_MESH_PATCH_WARN = 10_000


def _mesh_patch_feature(obj, user_scale, data):
    """Export feature for a leftover mesh patch: world-space triangles."""
    mesh = obj.data
    mesh.calc_loop_triangles()
    mw = obj.matrix_world
    s = user_scale
    verts = []
    for v in mesh.vertices:
        wv = mw @ v.co
        verts.append((wv.x * s, wv.y * s, wv.z * s))
    tris = [tuple(t.vertices) for t in mesh.loop_triangles]
    rgb = tuple(obj.color[:3])
    color = rgb if any(abs(c - 1.0) > 1e-4 for c in rgb) else None
    op = data["op"] if "op" in data.keys() else "ADD"
    group = data["group"] if "group" in data.keys() else "AUTO"
    return {"kind": "MESH_PATCH", "name": obj.name,
            "params": {"verts": verts, "tris": tris},
            "color": color, "op": op, "cut": "THROUGH", "group": group}


def _feature_from_object(obj, user_scale):
    """Read an object's stored fit params and return an export feature dict.

    Applies any transform the object has received since it was created (so moving
    the clean object is honoured), then the user's unit scale.
    """
    data = obj.get("reverse")
    if data is None:
        return None
    kind = data["kind"]
    if kind == "MESH_PATCH":
        # The geometry is the object's own mesh; matrix_world already honours
        # any later moves, so the _xform delta machinery is unnecessary.
        return _mesh_patch_feature(obj, user_scale, data)
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
    for key in ("u_min", "u_max"):          # fillet arc angles — invariant under the frame
        if key in data.keys():
            params[key] = float(data[key])
    if kind == "EXTRUDE" and "profile" in data.keys():
        k = obj_scale * s
        params["profile"] = [
            [float(row[0]), float(row[1]) * k, float(row[2]) * k,
             float(row[3]) * k, float(row[4]) * k,
             float(row[5]) * k, float(row[6]) * k, float(row[7])]
            for row in data["profile"]
        ]
    for key in _METADATA_KEYS:
        if key in data.keys():
            params[key] = data[key]
    # Counterbore / countersink recess params: radius/depth scale, angle does not.
    for key in ("cbore_radius", "cbore_depth"):
        if key in data.keys():
            params[key] = float(data[key]) * obj_scale * s
    if "csink_angle" in data.keys():
        params["csink_angle"] = float(data["csink_angle"])

    rgb = tuple(obj.color[:3])
    color = rgb if any(abs(c - 1.0) > 1e-4 for c in rgb) else None
    op = data["op"] if "op" in data.keys() else "ADD"
    cut = data["cut"] if "cut" in data.keys() else "THROUGH"
    group = data["group"] if "group" in data.keys() else "MANUAL"
    return {"kind": kind, "name": obj.name, "params": params, "color": color,
            "op": op, "cut": cut, "group": group}


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
    ordered_booleans: BoolProperty(
        name="Booleans in stack order",
        description=(
            "Apply ADD/SUBTRACT in the feature-stack order, so an ADD placed "
            "after a cut refills it (e.g. a boss inside a pocket). Off = legacy: "
            "fuse every ADD first, then apply all cutters (OCCT only)"
        ),
        default=True,
    )
    cutter_overshoot: FloatProperty(
        name="Cutter overshoot",
        description=(
            "Extend subtractive cylinders/cones by this fraction at each end so a "
            "hole cuts cleanly through coplanar faces (OCCT only). 0 disables"
        ),
        default=0.05, min=0.0, max=1.0, subtype="FACTOR",
    )
    auto_stitch: BoolProperty(
        name="Auto-stitch shared edges",
        description=(
            "Fuse the additive solids and unify coincident faces so abutting "
            "features share real edges (one box instead of two islands). OCCT only"
        ),
        default=False,
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
        description=(
            "Max gap between faces that the watertight pass will stitch closed, "
            "in STEP output units (after scaling)"
        ),
        default=0.01, min=0.0, max=100.0, precision=4,
    )
    unit: EnumProperty(
        name="Unit",
        description="Length unit declared in the STEP file",
        items=[("MM", "Millimeters", ""), ("M", "Meters", ""), ("IN", "Inches", "")],
        default="MM",
    )
    scale_mode: EnumProperty(
        name="Scale from",
        description="How the Blender-unit → STEP-unit coordinate scale is chosen",
        items=[
            ("SCENE", "Scene units",
             "Derive from the scene's unit settings (1 BU = Unit Scale meters) "
             "and the STEP unit above. Scenes with unit system 'None' pass "
             "coordinates through unchanged"),
            ("MANUAL", "Manual",
             "Use the Scale factor below"),
        ],
        default="SCENE",
    )
    scale: FloatProperty(
        name="Scale",
        description=(
            "Factor applied to all coordinates (e.g. 1000 to write metres as mm). "
            "Only used when Scale from is Manual"
        ),
        default=1.0, min=1e-6, max=1e6,
    )
    use_selection: BoolProperty(
        name="Selected only",
        description="Export only selected Reverse objects (otherwise all in the scene)",
        default=False,
    )
    group_filter: EnumProperty(
        name="Feature set",
        description="Which set of fitted primitives to export",
        items=[
            ("ALL", "All", "Export every fitted primitive"),
            ("MANUAL", "Manual only", "Export only the hand-fit feature stack"),
            ("BUILD", "Built only", "Export only the forward-built STEP primitives"),
            ("AUTO", "Auto only", "Export only the whole-mesh surface auto-decomposed set"),
            ("BOOL", "Solid only", "Export only the volumetric solid/boolean set"),
        ],
        default="ALL",
    )
    write_pmi_sidecar: BoolProperty(
        name="Write PMI sidecar",
        description=(
            "Also write .pmi.json and .pmi.csv next to the STEP with each feature's "
            "dimensions (radii, diameters, lengths, angles, threads) and pairwise "
            "relationships (axis angles, hole spacing)"
        ),
        default=False,
    )
    semantic_pmi: BoolProperty(
        name="Embed semantic PMI",
        description=(
            "Embed AP242 semantic dimensions (DIMENSIONAL_SIZE) in the STEP so CAD "
            "reads diameters/lengths as queryable PMI (pure-Python writer only)"
        ),
        default=False,
    )
    include_leftovers: BoolProperty(
        name="Include leftover patches",
        description=(
            "Export 'Reverse_Leftover' mesh patches (faces no primitive explained) "
            "as faceted planar faces so the STEP contains the complete part"
        ),
        default=True,
    )
    py_cutters: EnumProperty(
        name="Cutters (pure Python)",
        description=(
            "The pure-Python writer has no boolean kernel, so SUBTRACT features "
            "cannot be cut from the part. Choose what to write instead"
        ),
        items=[
            ("MARK", "Include marked",
             "Write cutters as red 'cutter:' reference solids so they are visibly "
             "not part material"),
            ("SKIP", "Skip",
             "Omit subtractive features from the file entirely"),
            ("SOLID", "Include as solids",
             "Legacy: write cutters as plain additive solids (holes appear filled)"),
        ],
        default="MARK",
    )

    @classmethod
    def poll(cls, context):
        return any("reverse" in o for o in context.scene.objects)

    def _effective_scale(self, context):
        if self.scale_mode == "MANUAL":
            return self.scale
        us = context.scene.unit_settings
        return units.effective_scale(self.unit, system=us.system,
                                     scale_length=us.scale_length)

    def invoke(self, context, event):
        # Default the STEP unit to the scene's display unit, unless the caller
        # already chose one explicitly.
        if not self.properties.is_property_set("unit"):
            us = context.scene.unit_settings
            self.unit = units.step_unit_for_scene(us.system, us.length_unit)
        return super().invoke(context, event)

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        ensure_occt_on_path()
        occ_ok = occ_export.is_available()
        will_use_occt = occ_ok and self.backend != "PUREPYTHON"

        layout.prop(self, "backend")
        if not will_use_occt:
            warn = layout.column(align=True)
            warn.alert = True
            if self.backend == "OCCT" and not occ_ok:
                warn.label(text="OCCT not installed — will fall back", icon="ERROR")
            warn.label(text="Pure-Python writer: cutters are NOT cut,", icon="ERROR")
            warn.label(text="no merging, watertightness or validation")
            layout.prop(self, "py_cutters")

        box = layout.box()
        box.enabled = will_use_occt
        box.label(text="Booleans / healing (OCCT)")
        box.prop(self, "merge_solids")
        box.prop(self, "ordered_booleans")
        box.prop(self, "cutter_overshoot")
        box.prop(self, "auto_stitch")
        box.prop(self, "make_watertight")
        box.prop(self, "sew_tolerance")

        layout.prop(self, "unit")
        layout.prop(self, "scale_mode")
        row = layout.row()
        row.enabled = self.scale_mode == "MANUAL"
        row.prop(self, "scale")
        eff = self._effective_scale(context)
        layout.label(text=f"Effective scale: 1 BU → {eff:g} {self.unit.lower()}")

        layout.prop(self, "use_selection")
        layout.prop(self, "group_filter")
        layout.prop(self, "include_leftovers")
        layout.prop(self, "write_pmi_sidecar")
        layout.prop(self, "semantic_pmi")

    def execute(self, context):
        # Script back-compat: an explicit scale= without a scale_mode= keeps its
        # old meaning (a manual factor) rather than being silently ignored.
        if (self.properties.is_property_set("scale")
                and not self.properties.is_property_set("scale_mode")):
            self.scale_mode = "MANUAL"
        eff_scale = self._effective_scale(context)

        sources = context.selected_objects if self.use_selection else context.scene.objects
        features = []
        drift_warnings = []
        for o in sources:
            if o.type == "MESH" and "reverse" in o:
                if self.group_filter != "ALL":
                    grp = o["reverse"]["group"] if "group" in o["reverse"].keys() else "MANUAL"
                    if grp != self.group_filter:
                        continue
                feat = _feature_from_object(o, eff_scale)
                if feat is not None:
                    features.append(feat)
                    msg = forward.drift_status(o)
                    if msg:
                        drift_warnings.append(f"⚠ {o.name}: {msg}")
        for msg in drift_warnings:
            self.report({"WARNING"}, msg)

        if not self.include_leftovers:
            features = [f for f in features if f["kind"] != "MESH_PATCH"]
        big = sum(len(f["params"]["tris"]) for f in features
                  if f["kind"] == "MESH_PATCH")
        if big > _MESH_PATCH_WARN:
            self.report({"WARNING"},
                        f"Leftover patches carry {big} triangles — the STEP will be "
                        "large; consider remeshing or disabling 'Include leftover patches'")

        if not features:
            self.report({"WARNING"}, "No fitted (Reverse) objects found to export")
            return {"CANCELLED"}

        # Order features by their position in the feature stack (the order the
        # user arranged in the panel); objects not in the stack keep scene order.
        rank = {f.object_name: i for i, f in enumerate(context.scene.reverse.features)}
        features.sort(key=lambda f: rank.get(f["name"], len(rank)))

        name = os.path.splitext(os.path.basename(self.filepath))[0] or "Reverse"

        if self.write_pmi_sidecar:
            try:
                jp, cp = pmi_export.write_sidecar(features, self.filepath)
                self.report({"INFO"}, f"PMI sidecar → {os.path.basename(jp)}, {os.path.basename(cp)}")
            except Exception as exc:
                self.report({"WARNING"}, f"PMI sidecar failed: {exc}")

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
                                         sew_tol=self.sew_tolerance,
                                         auto_stitch=self.auto_stitch,
                                         ordered=self.ordered_booleans)
                context.scene.reverse.last_report = "\n".join(
                    drift_warnings + [_format_report(info)])
                self.report({"INFO"}, f"Exported via OCCT: {info}")
                return {"FINISHED"}
            except Exception as exc:
                self.report({"WARNING"}, f"OCCT export failed ({exc}); using pure-Python")

        n_cut = sum(1 for f in features if f.get("op") == "SUBTRACT")
        if n_cut == len(features) and self.py_cutters == "SKIP":
            self.report({"WARNING"},
                        "All features are cutters and 'Cutters' is set to Skip — nothing to export")
            return {"CANCELLED"}

        text = step_export.build_step(
            features,
            unit=self.unit,
            product_name=name,
            timestamp=datetime.datetime.now().isoformat(timespec="seconds"),
            filename=os.path.basename(self.filepath),
            pmi=self.semantic_pmi,
            cutter_mode=self.py_cutters,
        )
        with open(self.filepath, "w", encoding="ascii", errors="replace") as fp:
            fp.write(text)

        report_lines = list(drift_warnings)
        if n_cut:
            verb = {"SKIP": "skipped",
                    "MARK": "written as red 'cutter:' reference solids",
                    "SOLID": "written as plain solids (holes appear filled)"}[self.py_cutters]
            msg = f"Pure-Python writer cannot subtract — {n_cut} cutter(s) {verb}"
            self.report({"WARNING"}, msg)
            report_lines.append(msg)
        report_lines.append(
            "Validation (volumes / watertightness) requires the OCCT kernel.\n"
            "Install it from the panel to get a per-solid report.")
        context.scene.reverse.last_report = "\n".join(report_lines)
        n_written = len(features) - (n_cut if self.py_cutters == "SKIP" else 0)
        self.report({"INFO"}, f"Exported {n_written} primitives → {os.path.basename(self.filepath)}")
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
        item.group = data.get("group", "MANUAL")
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
    REVERSE_OT_add_primitive,
    REVERSE_OT_edit_params,
    REVERSE_OT_rebuild_feature,
    REVERSE_OT_bake_scale,
    REVERSE_OT_fit_selection,
    REVERSE_OT_auto_decompose,
    REVERSE_OT_clear_auto_set,
    REVERSE_OT_solid_decompose,
    REVERSE_OT_clear_solid_set,
    REVERSE_OT_select_similar,
    REVERSE_OT_move_feature,
    REVERSE_OT_remove_feature,
    REVERSE_OT_refit_feature,
    REVERSE_OT_propagate_pattern,
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
