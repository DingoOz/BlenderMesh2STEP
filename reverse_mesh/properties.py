# SPDX-License-Identifier: GPL-3.0-or-later
"""Scene-level settings and the running list of fitted features."""

import math

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
    ("FILLET", "Fillet", "Edge fillet / round → a trimmed partial cylinder", "MOD_BEVEL", 7),
    ("EXTRUDE", "Extrude", "Extruded planar profile (prism) — select the whole "
     "prism including its end caps", "MOD_SOLIDIFY", 8),
    ("REVOLVE", "Revolve", "Solid of revolution (lathe part) — select the whole "
     "revolved body; explicit only, never chosen by Auto-detect", "MOD_SCREW", 9),
]


BUILD_PRIMITIVE_ITEMS = [
    ("BOX", "Box", "A cuboid solid", "MESH_CUBE", 0),
    ("CYLINDER", "Cylinder", "A cylindrical solid", "MESH_CYLINDER", 1),
    ("CONE", "Cone", "A conical / tapered solid (frustum)", "MESH_CONE", 2),
    ("SPHERE", "Sphere", "A spherical solid", "MESH_UVSPHERE", 3),
    ("TORUS", "Torus", "A toroidal solid (ring)", "MESH_TORUS", 4),
    ("EXTRUDE", "Extrude (N-gon)", "An extruded regular-polygon prism", "MOD_SOLIDIFY", 5),
    ("REVOLVE", "Ring (washer)", "A rectangular section revolved about the axis "
     "— washers, spacers, bushings", "MOD_SCREW", 6),
]


# Guard against update-callback recursion: sync_build_params() writes the
# PropertyGroup programmatically, which would otherwise re-fire the rebuild.
_updating_build_params = False


def _on_build_param_update(self, context):
    """A dimension field changed: write through to obj["reverse"] and rebuild."""
    global _updating_build_params
    if _updating_build_params:
        return
    obj = self.id_data
    data = obj.get("reverse") if obj else None
    if data is None:
        return
    from . import forward

    kind = data["kind"]
    fields = forward.PARAM_FIELDS.get(kind)
    if not fields:
        return
    new = {k: data[k] for k in data.keys()}
    for key, _label in fields:
        new[key] = float(getattr(self, key))
    if kind == "CONE":
        h = new["height"]
        new["half_angle"] = (math.atan(abs(new["radius2"] - new["radius1"]) / h)
                             if h > 1e-12 else 0.0)
    if kind == "EXTRUDE":
        forward.refresh_extrude_profile(new)   # N-gon profile follows the radius
    if kind == "REVOLVE":
        forward.refresh_revolve_profile(new)   # ring section follows the radii
    # Reassign the whole dict: nested IDProperty writes don't reliably tag updates.
    obj["reverse"] = new
    segments = 48
    scene = getattr(context, "scene", None)
    if scene is not None and hasattr(scene, "reverse"):
        segments = scene.reverse.segments
        from .fitting.primitives import summarize
        for f in scene.reverse.features:
            if f.object_name == obj.name:
                f.summary = summarize(kind, new)
    forward.rebuild_object(obj, segments)


def sync_build_params(obj):
    """Copy obj["reverse"] dimension values into obj.reverse_build (no rebuild).

    Must be called from an operator (or other writable context), never from a
    panel ``draw()`` — Blender prohibits property writes there.
    """
    global _updating_build_params
    data = obj.get("reverse") if obj else None
    if data is None:
        return False
    from . import forward

    fields = forward.PARAM_FIELDS.get(data["kind"])
    if not fields:
        return False
    _updating_build_params = True
    try:
        for key, _label in fields:
            if key in data.keys():
                setattr(obj.reverse_build, key, float(data[key]))
    finally:
        _updating_build_params = False
    return True


def build_params_synced(obj):
    """True when obj.reverse_build mirrors obj["reverse"] (read-only, draw-safe)."""
    data = obj.get("reverse") if obj else None
    if data is None:
        return False
    from . import forward

    fields = forward.PARAM_FIELDS.get(data["kind"])
    if not fields:
        return False
    for key, _label in fields:
        if key not in data.keys():
            return False
        stored = float(data[key])
        if abs(getattr(obj.reverse_build, key) - stored) > 1e-6 * max(1.0, abs(stored)):
            return False
    return True


class ReverseBuildParams(PropertyGroup):
    """Live-editable dimensions mirroring the active object's stored params.

    Drawn by the Build panel; each edit writes through to ``obj["reverse"]``
    and regenerates the mesh (see :func:`_on_build_param_update`).
    """

    radius: FloatProperty(name="Radius", min=1e-6, default=1.0, unit="LENGTH",
                          update=_on_build_param_update)
    height: FloatProperty(name="Height", min=1e-6, default=1.0, unit="LENGTH",
                          update=_on_build_param_update)
    radius1: FloatProperty(name="Base radius", min=0.0, default=1.0, unit="LENGTH",
                           update=_on_build_param_update)
    radius2: FloatProperty(name="Top radius", min=0.0, default=0.5, unit="LENGTH",
                           update=_on_build_param_update)
    major_radius: FloatProperty(name="Major radius", min=1e-6, default=1.0,
                                unit="LENGTH", update=_on_build_param_update)
    minor_radius: FloatProperty(name="Minor radius", min=1e-6, default=0.25,
                                unit="LENGTH", update=_on_build_param_update)
    hx: FloatProperty(name="Half X", min=1e-6, default=1.0, unit="LENGTH",
                      update=_on_build_param_update)
    hy: FloatProperty(name="Half Y", min=1e-6, default=1.0, unit="LENGTH",
                      update=_on_build_param_update)
    hz: FloatProperty(name="Half Z", min=1e-6, default=1.0, unit="LENGTH",
                      update=_on_build_param_update)


def _on_thread_update(self, context):
    # Mirror the thread spec onto the feature's object so it round-trips to STEP.
    obj = bpy.data.objects.get(self.object_name) if self.object_name else None
    if obj is None or "reverse" not in obj:
        return
    if self.thread_spec:
        obj["reverse"]["thread_spec"] = self.thread_spec
    elif "thread_spec" in obj["reverse"]:
        del obj["reverse"]["thread_spec"]


def _on_hole_update(self, context):
    # Mirror counterbore/countersink preset params onto the object for export.
    obj = bpy.data.objects.get(self.object_name) if self.object_name else None
    if obj is None or "reverse" not in obj:
        return
    rev = obj["reverse"]
    if self.hole_preset != "NONE":
        rev["hole_preset"] = self.hole_preset
        rev["cbore_radius"] = self.cbore_radius
        rev["cbore_depth"] = self.cbore_depth
        rev["csink_angle"] = self.csink_angle
    else:
        for k in ("hole_preset", "cbore_radius", "cbore_depth", "csink_angle"):
            if k in rev:
                del rev[k]


def _on_heatmap_toggle(self, context):
    # Clear any drawn heatmap when the user switches it off (it rebuilds on the
    # next fit). Imported lazily to avoid a module-load cycle.
    from . import overlay
    if not self.show_heatmap:
        overlay.clear_prefix("heatmap:")


class ReverseFeature(PropertyGroup):
    """One fitted primitive, shown in the session feature list."""

    kind: StringProperty(name="Kind")
    group: StringProperty(name="Group", default="MANUAL")   # "MANUAL" | "AUTO" (whole-mesh decompose)
    summary: StringProperty(name="Summary")
    rms: FloatProperty(name="RMS")
    max_error: FloatProperty(name="Max error")
    object_name: StringProperty(name="Object")
    operation: StringProperty(name="Operation", default="ADD")
    cut_mode: StringProperty(name="Cut mode", default="THROUGH")
    runner_up: StringProperty(name="Runner-ups")   # AUTO tie-break, e.g. "CYLINDER 0% | SPHERE 0.4%"
    source_object: StringProperty(name="Source object")   # mesh the feature was fit from
    source_faces: StringProperty(name="Source faces")     # comma-joined face indices (for re-fit)
    thread_spec: StringProperty(                          # e.g. "M8x1.25"; annotates STEP
        name="Thread", description="Thread designation for this hole/shaft (e.g. M8x1.25)",
        update=_on_thread_update)
    hole_preset: EnumProperty(
        name="Hole",
        description="Recess at the open end of a subtractive cylindrical hole (OCCT export)",
        items=[
            ("NONE", "Plain", "A plain hole"),
            ("COUNTERBORE", "Counterbore", "Flat-bottomed wider recess (for a cap screw head)"),
            ("COUNTERSINK", "Countersink", "Tapered recess (for a flat-head screw)"),
        ],
        default="NONE",
        update=_on_hole_update,
    )
    cbore_radius: FloatProperty(name="Recess radius", default=0.0, min=0.0,
                                update=_on_hole_update)
    cbore_depth: FloatProperty(name="Counterbore depth", default=0.0, min=0.0,
                               update=_on_hole_update)
    csink_angle: FloatProperty(name="Countersink angle (°)", default=90.0, min=1.0, max=179.0,
                               update=_on_hole_update)


class ReverseSettings(PropertyGroup):
    primitive_type: EnumProperty(
        name="Primitive",
        description="Which analytic surface to fit to the selected faces",
        items=PRIMITIVE_ITEMS,
        default="AUTO",
    )
    build_primitive_type: EnumProperty(
        name="Primitive",
        description="Which STEP solid to add (forward building block)",
        items=BUILD_PRIMITIVE_ITEMS,
        default="BOX",
    )
    build_extrude_sides: IntProperty(
        name="Sides",
        description="Profile side count for a forward-built extruded prism",
        default=6, min=3, max=64,
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
    select_similar_angle: FloatProperty(
        name="Similar crease angle (°)",
        description=(
            "Select Similar grows across edges whose face-normal angle is below "
            "this — higher follows more curvature, lower stops at gentler creases"
        ),
        default=20.0,
        min=1.0,
        max=180.0,
    )
    snap_enabled: BoolProperty(
        name="Snap dimensions",
        description=(
            "Round fitted radii/lengths to a nice value when they're already very "
            "close (e.g. 19.98 → 20.0), so the STEP carries manufacturable numbers"
        ),
        default=False,
    )
    snap_preset: EnumProperty(
        name="Snap to",
        description="Grid the fitted dimensions snap to",
        items=[
            ("0.1", "0.1", "Nearest 0.1"),
            ("0.5", "0.5", "Nearest 0.5"),
            ("1.0", "1.0", "Nearest 1.0"),
            ("CUSTOM", "Custom", "Nearest multiple of a custom step"),
        ],
        default="1.0",
    )
    snap_step: FloatProperty(
        name="Custom step",
        description="Snap grid used when 'Snap to' is Custom",
        default=1.0,
        min=1e-6,
        max=1e6,
        precision=4,
    )
    use_ransac: BoolProperty(
        name="Reject outliers (robust fit)",
        description=(
            "Trim stray faces/points that deviate from the surface and refit on "
            "the rest — survives a slightly dirty selection (a chamfer, an odd "
            "triangle) without abandoning the clean-mesh sweet spot"
        ),
        default=False,
    )
    ransac_threshold: FloatProperty(
        name="Outlier threshold",
        description="Points/faces beyond this fraction of the region size are dropped",
        default=0.02,
        min=1e-4,
        max=1.0,
        precision=4,
    )
    show_heatmap: BoolProperty(
        name="Fit-quality heatmap",
        description=(
            "After fitting, colour the selected faces by how far each deviates "
            "from the fitted surface (green = exact, red = off by the tolerance) "
            "so you can spot faces that don't belong to the primitive"
        ),
        default=False,
        update=_on_heatmap_toggle,
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

    # --- Whole-mesh auto-decompose (heavy, global-optimization path) -----------
    decompose_angles: StringProperty(
        name="Crease angles (°)",
        description=(
            "Coarse→fine crease angles swept to build competing primitive "
            "hypotheses. Comma-separated; coarse first (e.g. '40, 25, 12, 6')"
        ),
        default="40, 25, 12, 6",
    )
    decompose_tolerance: FloatProperty(
        name="Decompose tolerance",
        description="Max relative RMS for a region's primitive to be accepted",
        default=0.02, min=1e-4, max=1.0, precision=4,
    )
    decompose_min_faces: IntProperty(
        name="Min faces / region",
        description=(
            "Skip regions smaller than this many faces. 1 fits even coarse meshes "
            "(a plain cube → 6 planes); raise it to ignore small noisy patches on "
            "dense scans"
        ),
        default=1, min=1, max=100000,
    )
    decompose_min_coverage: FloatProperty(
        name="Min coverage",
        description="Warn if fewer than this fraction of the mesh area gets explained",
        default=0.4, min=0.0, max=1.0, subtype="FACTOR",
    )
    decompose_merge: BoolProperty(
        name="Merge same-kind",
        description=(
            "After the greedy cover, collapse adjacent same-kind primitives "
            "(coplanar planes, coaxial cylinders) when it lowers the energy"
        ),
        default=True,
    )
    decompose_lambda: FloatProperty(
        name="Merge pressure (λ)",
        description=(
            "Cost charged per kept primitive. Higher = fewer, larger primitives "
            "(more aggressive merging); lower = more, tighter-fitting primitives"
        ),
        default=0.01, min=0.0, max=10.0, precision=4,
    )
    decompose_mu: FloatProperty(
        name="Coverage pressure (μ)",
        description=(
            "Cost charged per unit of unexplained area. Higher = try harder to "
            "cover everything; lower = leave freeform regions out"
        ),
        default=1.0, min=0.0, max=100.0, precision=3,
    )
    decompose_nu: FloatProperty(
        name="Boundary smoothness (ν)",
        description="Cost charged for a fragmented assignment border",
        default=0.02, min=0.0, max=10.0, precision=4,
    )
    decompose_keep_leftovers: BoolProperty(
        name="Keep leftover faces",
        description=(
            "Copy the faces no primitive explained into a 'Reverse_Leftover' mesh "
            "object so the STEP export contains the complete part (the patch is "
            "written as faceted planar faces, not an analytic surface). Its "
            "boundary only meets the fitted surfaces within the fit deviation — "
            "raise the export's sew tolerance if a watertight pass reports gaps"
        ),
        default=False,
    )

    # --- Whole-mesh solid/boolean decompose (volumetric, additive CSG) ---------
    solid_resolution: IntProperty(
        name="Volume resolution",
        description=(
            "Voxel resolution along the longest axis when sampling the solid's "
            "volume. Higher is more accurate but much slower"
        ),
        default=40, min=12, max=128,
    )
    solid_max_primitives: IntProperty(
        name="Max primitives",
        description="Stop after fitting this many solids (union budget)",
        default=16, min=1, max=64,
    )
    solid_min_coverage: FloatProperty(
        name="Min coverage",
        description="Warn if the union covers less than this fraction of the volume",
        default=0.8, min=0.0, max=1.0, subtype="FACTOR",
    )

    features: CollectionProperty(type=ReverseFeature)
    active_feature: IntProperty(name="Active feature", default=0)

    # Last export's validation report (one line per solid), filled by the OCCT
    # path; shown in the Validation panel. Empty until the first export.
    last_report: StringProperty(name="Last export report", default="")


classes = (ReverseFeature, ReverseSettings, ReverseBuildParams)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.reverse = PointerProperty(type=ReverseSettings)
    bpy.types.Object.reverse_build = PointerProperty(type=ReverseBuildParams)


def unregister():
    del bpy.types.Object.reverse_build
    del bpy.types.Scene.reverse
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
