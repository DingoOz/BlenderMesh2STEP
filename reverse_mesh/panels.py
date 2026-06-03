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
        row.label(text=item.summary or item.kind, icon=icon_for.get(item.kind, "DOT"))
        row.label(text=f"RMS {item.rms:.3g}")


class REVERSE_PT_main(Panel):
    bl_label = "Reverse — Mesh to Parametric"
    bl_idname = "REVERSE_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Reverse"

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

        box.prop(settings, "snap_enabled")
        snap = box.column(align=True)
        snap.enabled = settings.snap_enabled
        snap.prop(settings, "snap_preset", text="Snap to")
        if settings.snap_preset == "CUSTOM":
            snap.prop(settings, "snap_step")


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

        box = layout.box()
        box.label(text="Export", icon="EXPORT")
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


classes = (REVERSE_UL_features, REVERSE_PT_main, REVERSE_PT_features, REVERSE_PT_report)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
