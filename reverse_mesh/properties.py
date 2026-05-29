# SPDX-License-Identifier: GPL-3.0-or-later
"""Scene-level settings and the running list of fitted features."""

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import PropertyGroup


PRIMITIVE_ITEMS = [
    ("AUTO", "Auto-detect", "Try every primitive, keep the best fit", "SHADERFX", 0),
    ("PLANE", "Plane", "Flat face", "MESH_PLANE", 1),
    ("BOX", "Box", "Oriented box / cuboid from planar faces", "MESH_CUBE", 6),
    ("CYLINDER", "Cylinder", "Cylindrical face", "MESH_CYLINDER", 2),
    ("CONE", "Cone", "Conical / tapered face (experimental)", "MESH_CONE", 3),
    ("SPHERE", "Sphere", "Spherical face", "MESH_UVSPHERE", 4),
    ("TORUS", "Torus", "Toroidal face / ring (best on full rings)", "MESH_TORUS", 5),
]


class ReverseFeature(PropertyGroup):
    """One fitted primitive, shown in the session feature list."""

    kind: StringProperty(name="Kind")
    summary: StringProperty(name="Summary")
    rms: FloatProperty(name="RMS")
    max_error: FloatProperty(name="Max error")
    object_name: StringProperty(name="Object")
    operation: StringProperty(name="Operation", default="ADD")
    cut_mode: StringProperty(name="Cut mode", default="THROUGH")


class ReverseSettings(PropertyGroup):
    primitive_type: EnumProperty(
        name="Primitive",
        description="Which analytic surface to fit to the selected faces",
        items=PRIMITIVE_ITEMS,
        default="AUTO",
    )
    create_object: BoolProperty(
        name="Create clean object",
        description="Generate a new analytic primitive object from the fit",
        default=True,
    )
    default_operation: EnumProperty(
        name="Role",
        description="Boolean role for newly fitted primitives (used by OCCT export)",
        items=[
            ("ADD", "Add", "Material / base body", "ADD", 0),
            ("SUBTRACT", "Subtract", "A cutter to be subtracted (e.g. a hole)", "REMOVE", 1),
        ],
        default="ADD",
    )
    default_cut_mode: EnumProperty(
        name="Cut",
        description="For subtractive cutters: through-hole, or blind pocket (keeps depth)",
        items=[
            ("THROUGH", "Through", "Overshoot both ends so the hole opens on coplanar faces"),
            ("BLIND", "Blind", "Overshoot only the open end; keep the pocket depth exact"),
        ],
        default="THROUGH",
    )
    segment_regions: BoolProperty(
        name="Segment regions",
        description=(
            "Split the selection into smooth-connected regions and fit each "
            "separately (e.g. a cube becomes 6 planes, a cylinder its side + 2 caps)"
        ),
        default=False,
    )
    segment_angle: FloatProperty(
        name="Crease angle (°)",
        description="Adjacent faces above this normal angle are treated as separate surfaces",
        default=20.0,
        min=1.0,
        max=180.0,
    )
    segments: IntProperty(
        name="Segments",
        description="Tessellation resolution of generated round primitives",
        default=48,
        min=8,
        max=256,
    )
    tolerance: FloatProperty(
        name="Tolerance",
        description="Warn when the fit's RMS exceeds this fraction of the region size",
        default=0.02,
        min=0.0,
        max=1.0,
        precision=4,
    )

    features: CollectionProperty(type=ReverseFeature)
    active_feature: IntProperty(name="Active feature", default=0)


classes = (ReverseFeature, ReverseSettings)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.reverse = PointerProperty(type=ReverseSettings)


def unregister():
    del bpy.types.Scene.reverse
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
