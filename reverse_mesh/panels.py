# SPDX-License-Identifier: GPL-3.0-or-later
"""N-panel UI in the 3D Viewport sidebar (category: Reverse)."""

import bpy
from bpy.types import Panel, UIList


class REVERSE_UL_features(UIList):
    """List view of fitted features for this session."""

    def draw_item(self, context, layout, data, item, icon, active_data, active_prop, index):
        icon_for = {
            "PLANE": "MESH_PLANE",
            "BOX": "MESH_CUBE",
            "CYLINDER": "MESH_CYLINDER",
            "CONE": "MESH_CONE",
            "SPHERE": "MESH_UVSPHERE",
            "TORUS": "MESH_TORUS",
        }
        row = layout.row(align=True)
        op_icon = "REMOVE" if item.operation == "SUBTRACT" else "ADD"
        row.label(text="", icon=op_icon)
        label = item.summary or item.kind
        if item.group == "AUTO":
            label = f"[A] {label}"           # whole-mesh surface auto-decompose set
        elif item.group == "BOOL":
            label = f"[S] {label}"           # whole-mesh solid/boolean set
        elif item.group == "BUILD":
            label = f"[B] {label}"           # forward-built STEP primitive
        row.label(text=label, icon=icon_for.get(item.kind, "DOT"))
        # Built primitives are exact by construction — an RMS would be noise.
        row.label(text="exact" if item.group == "BUILD" else f"RMS {item.rms:.3g}")


class REVERSE_PT_build(Panel):
    """Forward modeling: drop STEP-primitive solids and edit them parametrically.

    Everything created here carries the same param schema as fitted primitives,
    so it shares the feature stack and is always STEP-exportable.
    """

    bl_label = "Build — STEP Primitives"
    bl_idname = "REVERSE_PT_build"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Reverse"
    bl_order = 0

    def draw(self, context):
        from . import forward
        from .properties import build_params_synced

        layout = self.layout
        settings = context.scene.reverse

        col = layout.column(align=True)
        col.prop(settings, "build_primitive_type")
        col.prop(settings, "default_operation", text="Role")
        if settings.default_operation == "SUBTRACT":
            col.prop(settings, "default_cut_mode", text="Cut")

        icon_for = {"BOX": "MESH_CUBE", "CYLINDER": "MESH_CYLINDER",
                    "CONE": "MESH_CONE", "SPHERE": "MESH_UVSPHERE",
                    "TORUS": "MESH_TORUS"}
        layout.operator("reverse.add_primitive", text="Add Primitive",
                        icon=icon_for.get(settings.build_primitive_type, "PLUS"))

        obj = context.active_object
        data = obj.get("reverse") if obj else None
        if data is None:
            return
        kind = data["kind"]
        fields = forward.PARAM_FIELDS.get(kind)

        box = layout.box()
        box.label(text=f"{obj.name} · {kind.title()}", icon=icon_for.get(kind, "DOT"))
        if fields:
            if build_params_synced(obj):
                col = box.column(align=True)
                for key, label in fields:
                    col.prop(obj.reverse_build, key, text=label)
            else:
                # draw() may not write properties, so syncing the editable
                # fields from the stored params takes one click.
                box.operator("reverse.edit_params", icon="OPTIONS")

        drift = forward.drift_status(obj)
        if drift:
            warn = box.column(align=True)
            warn.alert = True
            warn.label(text="Out of sync with parameters:", icon="ERROR")
            for line in drift.split(" — "):
                warn.label(text=line)
            box.operator("reverse.rebuild_feature", icon="FILE_REFRESH")
        if obj.mode == "OBJECT" and any(abs(c - 1.0) > 1e-6 for c in obj.scale):
            box.operator("reverse.bake_scale", icon="FULLSCREEN_EXIT")


class REVERSE_PT_main(Panel):
    bl_label = "Reverse — Mesh to Parametric"
    bl_idname = "REVERSE_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Reverse"
    bl_order = 1

    def draw(self, context):
        layout = self.layout
        settings = context.scene.reverse

        col = layout.column(align=True)
        col.prop(settings, "primitive_type")
        col.prop(settings, "default_operation", text="Role")
        if settings.default_operation == "SUBTRACT":
            col.prop(settings, "default_cut_mode", text="Cut")

        obj = context.active_object
        in_edit = obj and obj.type == "MESH" and obj.mode == "EDIT"
        if not in_edit:
            box = layout.box()
            box.label(text="Enter Edit Mode and select faces", icon="INFO")
        else:
            layout.operator("reverse.fit_selection", icon="SHADERFX")
            row = layout.row(align=True)
            row.operator("reverse.select_similar", icon="FACESEL", text="Select Similar")
            row.prop(settings, "select_similar_angle", text="")

        box = layout.box()
        box.label(text="Segmentation", icon="MOD_EDGESPLIT")
        box.prop(settings, "segment_regions")
        sub = box.column(align=True)
        sub.enabled = settings.segment_regions
        sub.prop(settings, "segment_angle")

        box = layout.box()
        box.label(text="Output", icon="OUTPUT")
        box.prop(settings, "create_object")
        sub = box.column(align=True)
        sub.enabled = settings.create_object
        sub.prop(settings, "segments")
        box.prop(settings, "tolerance")
        box.prop(settings, "show_heatmap")

        box.prop(settings, "use_ransac")
        rsub = box.column(align=True)
        rsub.enabled = settings.use_ransac
        rsub.prop(settings, "ransac_threshold")

        box.prop(settings, "snap_enabled")
        snap = box.column(align=True)
        snap.enabled = settings.snap_enabled
        snap.prop(settings, "snap_preset", text="Snap to")
        if settings.snap_preset == "CUSTOM":
            snap.prop(settings, "snap_step")

        # --- Whole-mesh auto-decompose (heavy, experimental) -------------------
        box = layout.box()
        box.alert = True                     # red tint flags the heavy/experimental path
        col = box.column(align=True)
        col.label(text="Whole-Mesh Auto-Decompose", icon="MODIFIER_DATA")
        col.label(text="Heavy: optimizes over the ENTIRE mesh", icon="ERROR")
        col.operator("reverse.auto_decompose", icon="SHADERFX",
                     text="Auto-Decompose Whole Mesh")
        col.operator("reverse.clear_auto_set", icon="TRASH", text="Clear Auto Set")
        sub = box.column(align=True)
        sub.prop(settings, "decompose_angles")
        sub.prop(settings, "decompose_tolerance")
        sub.prop(settings, "decompose_min_faces")
        sub.prop(settings, "decompose_min_coverage")
        row = box.row(align=True)
        row.prop(settings, "decompose_merge", toggle=True)
        row.prop(settings, "decompose_keep_leftovers", toggle=True)
        adv = box.column(align=True)
        adv.label(text="Energy weights (advanced):")
        adv.prop(settings, "decompose_lambda")
        adv.prop(settings, "decompose_mu")
        adv.prop(settings, "decompose_nu")

        # --- Whole-mesh SOLID / boolean decompose (volumetric, additive CSG) ---
        box = layout.box()
        box.alert = True
        col = box.column(align=True)
        col.label(text="Solid Decompose (Boolean)", icon="MOD_BOOLEAN")
        col.label(text="Heavy: fills the VOLUME with solids", icon="ERROR")
        col.operator("reverse.solid_decompose", icon="META_BALL",
                     text="Auto-Decompose Solid (Boolean)")
        col.operator("reverse.clear_solid_set", icon="TRASH", text="Clear Solid Set")
        sub = box.column(align=True)
        sub.prop(settings, "solid_resolution")
        sub.prop(settings, "solid_max_primitives")
        sub.prop(settings, "solid_min_coverage")
        box.label(text="Tip: export with OCCT 'Merge into one solid'", icon="INFO")


class REVERSE_PT_features(Panel):
    bl_label = "Fitted Features"
    bl_idname = "REVERSE_PT_features"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Reverse"
    bl_parent_id = "REVERSE_PT_main"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.reverse

        list_row = layout.row()
        list_row.template_list(
            "REVERSE_UL_features", "",
            settings, "features",
            settings, "active_feature",
            rows=4,
        )
        # Reorder / re-fit / delete the stack — non-destructive editing column.
        col = list_row.column(align=True)
        col.operator("reverse.move_feature", text="", icon="TRIA_UP").direction = "UP"
        col.operator("reverse.move_feature", text="", icon="TRIA_DOWN").direction = "DOWN"
        col.separator()
        col.operator("reverse.refit_feature", text="", icon="FILE_REFRESH")
        col.operator("reverse.remove_feature", text="", icon="X")

        row = layout.row(align=True)
        row.operator("reverse.set_operation", text="Add", icon="ADD").operation = "ADD"
        row.operator("reverse.set_operation", text="Subtract", icon="REMOVE").operation = "SUBTRACT"

        # Cut mode only matters for subtractive features.
        active_sub = (0 <= settings.active_feature < len(settings.features)
                      and settings.features[settings.active_feature].operation == "SUBTRACT")
        if active_sub:
            row = layout.row(align=True)
            row.operator("reverse.set_cut_mode", text="Through").cut_mode = "THROUGH"
            row.operator("reverse.set_cut_mode", text="Blind").cut_mode = "BLIND"

        row = layout.row(align=True)
        row.operator("reverse.select_feature_object", icon="RESTRICT_SELECT_OFF", text="Select")
        row.operator("reverse.clear_features", icon="TRASH", text="Clear")

        layout.operator("reverse.propagate_pattern", icon="MOD_ARRAY",
                        text="Propagate Pattern (find matching holes)")

        if 0 <= settings.active_feature < len(settings.features):
            item = settings.features[settings.active_feature]
            box = layout.box()
            box.label(text=item.summary, icon="DOT")
            box.label(text=f"RMS: {item.rms:.5g}")
            box.label(text=f"Max error: {item.max_error:.5g}")
            role = "Subtract" if item.operation == "SUBTRACT" else "Add"
            role_txt = f"{role} · {item.cut_mode.title()}" if item.operation == "SUBTRACT" else role
            box.label(text=f"Role: {role_txt}",
                      icon="REMOVE" if item.operation == "SUBTRACT" else "ADD")
            if item.runner_up:
                box.label(text=f"Auto: {item.runner_up}", icon="SHADERFX")
            if item.kind in {"CYLINDER", "CONE"}:
                box.prop(item, "thread_spec", icon="MOD_SCREW")
            if item.kind == "CYLINDER" and item.operation == "SUBTRACT":
                box.prop(item, "hole_preset")
                if item.hole_preset == "COUNTERBORE":
                    box.prop(item, "cbore_radius")
                    box.prop(item, "cbore_depth")
                elif item.hole_preset == "COUNTERSINK":
                    box.prop(item, "cbore_radius", text="Countersink radius")
                    box.prop(item, "csink_angle")

        box = layout.box()
        box.label(text="Export", icon="EXPORT")
        # Show the set-filter hint once a machine-generated set exists.
        if any(f.group in {"AUTO", "BOOL"} for f in settings.features):
            box.label(text="Tip: pick the feature set in the export dialog", icon="FILTER")
        box.operator("reverse.export_step", text="Export STEP (AP242)", icon="FILE_CACHE")

        from . import occ_export
        from .operators import ensure_occt_on_path
        ensure_occt_on_path()
        if occ_export.is_available():
            row = box.row()
            row.label(text=f"OCCT ready ({occ_export.backend_name()})", icon="CHECKMARK")
        else:
            col = box.column(align=True)
            col.label(text="OCCT kernel not installed", icon="INFO")
            col.label(text="(optional: enables merged solids)")
            col.operator("reverse.install_occt", icon="IMPORT")


class REVERSE_PT_report(Panel):
    bl_label = "Validation Report"
    bl_idname = "REVERSE_PT_report"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Reverse"
    bl_parent_id = "REVERSE_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        report = context.scene.reverse.last_report
        if not report:
            layout.label(text="Export a STEP file to see validation", icon="INFO")
            return
        box = layout.box()
        for line in report.split("\n"):
            icon = "CHECKMARK" if ("valid" in line and "INVALID" not in line) or "✓" in line \
                else ("ERROR" if ("INVALID" in line or "NOT watertight" in line) else "DOT")
            box.label(text=line, icon=icon)


classes = (REVERSE_UL_features, REVERSE_PT_build, REVERSE_PT_main,
           REVERSE_PT_features, REVERSE_PT_report)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
