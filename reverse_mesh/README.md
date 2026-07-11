# Reverse — Mesh to Parametric (Blender extension)

Fit clean analytic CAD primitives — **plane, box, cylinder, cone, sphere, torus, extruded profile**
(the standard analytic-surface set, plus an oriented box for cuboid parts) — to
selected regions of a mesh, in the semi-automatic, human-in-the-loop style of the
[Reverse](https://github.com/nico-schluter/Reverse) Fusion 360 add-in.

This is **Tier 1** of [`../mesh-to-parametric-plan.md`](../mesh-to-parametric-plan.md):
geometry recovery, with **zero external dependencies** (pure NumPy, bundled with
Blender). STEP/BREP export via OpenCASCADE is a later tier.

## Why semi-automatic

A mesh has discarded the intent we want back: a 64-sided prism and a cylinder are
identical as triangles. Rather than guess, the tool lets *you* say "these faces
are a cylinder" and then recovers the exact analytic surface by least squares —
typically to machine precision on clean, Blender-authored meshes.

## Workflow

1. Select your mesh and enter **Edit Mode**.
2. Open the **Reverse** tab in the 3D Viewport sidebar (`N`).
3. Pick a **Primitive** (or leave it on *Auto-detect*).
4. Select the faces belonging to one feature.
5. Click **Fit Primitive to Selection**.
   - The reported **RMS** is the fit error in scene units; a warning fires if it
     exceeds the **Tolerance** (a fraction of the region size). A high RMS usually
     means the selection includes faces from a different feature.
   - With **Create clean object** on, a new analytic primitive is generated. Its
     exact fit parameters are stored on the object's `["reverse"]` custom property
     for a future STEP export.
6. Repeat per feature. The **Fitted Features** list tracks the session.

### Forward building (Build — STEP Primitives)

The **Build — STEP Primitives** panel is the inverse workflow: instead of
fitting primitives to a mesh, it creates them from typed dimensions. Built
objects carry the same `["reverse"]` param schema (group `"BUILD"`, shown as
`[B]` in the list), so roles, cut modes, hole presets, reordering and both STEP
export backends work identically — and the export is exact by construction.

- **Add Primitive** drops a box / cylinder / cone / sphere / torus at the 3D
  cursor with the current Role; tweak creation dimensions in the F9 redo panel.
- Selecting a built (or fitted) primitive shows its dimensions as live fields;
  edits regenerate the preview mesh and the stored parameters together. For an
  object whose fields aren't loaded yet, click **Edit Parameters** once.
- Drift guard: mesh edits or non-uniform scale on curved kinds are flagged in
  the panel and in the export report; **Rebuild from Parameters** restores the
  mesh from the stored values, and **Bake Scale into Parameters** folds a
  uniform object scale into the dimensions.

### Segmentation

A single primitive fit to a multi-surface selection is ambiguous — e.g. a cube's
8 corners lie exactly on a sphere, so Auto-detect would "see" a sphere. Enable
**Segment regions** to split the selection into smooth-connected patches (by a
crease angle) and fit each separately: a cube → 6 planes, a cylinder → its side
+ 2 caps. Without it, the tool warns when a selection clearly spans several
surfaces.

## STEP export (AP242)

Export fitted primitives as a **STEP AP242** file with genuine analytic surfaces
(not facets): **File ▸ Export ▸ Reverse STEP (AP242)**, or the **Export STEP**
button in the panel. Because each primitive has exact analytic parameters, the
writer emits real `CYLINDRICAL_SURFACE` / `CONICAL_SURFACE` / `SPHERICAL_SURFACE`
/ `TOROIDAL_SURFACE` / `PLANE` geometry as valid `MANIFOLD_SOLID_BREP` solids,
assembled into one part with units, product structure and per-feature colours —
all in pure Python, no kernel or dependencies.

- Each fitted primitive becomes its own analytic solid (planes export as bounded
  surfaces). The file is an assembly of those bodies.
- Verified: every generated solid imports into OpenCASCADE as topologically valid
  with the correct volume (see `tests/test_step.py`).

### Boolean reconstruction (Add / Subtract)

Each fitted primitive has a **Role**: *Add* (material / base body) or *Subtract*
(a cutter — e.g. a cylinder fitted to a hole, shown red in the viewport). Set the
role before fitting, or flip it later with the **Add / Subtract** buttons under
the feature list. With the OCCT backend, export fuses the Add solids and `Cut`s
the Subtract solids out, producing **one watertight solid with real holes**
(verified by volume: a 4×4×4 box drilled by an r=1 cylinder → 64 − 4π = 51.43).
Subtract requires OCCT (booleans need a kernel); the pure-Python writer ignores
roles and exports every primitive separately.

**Through vs. Blind.** Each subtractive cutter has a **Cut mode**:

- **Through** — overshoots *both* ends so the hole opens cleanly on coplanar faces
  (avoids the coincident-face boolean failure when a cutter's cap is flush with a
  base face).
- **Blind** — overshoots *only the open end* and keeps the pocket depth exact. The
  open end is detected automatically by testing which end of the cutter lies inside
  the base solid, so you don't have to worry about the cutter's axis direction.

Verified by volume: a box drilled *through* by an r=1 cylinder → 64 − 4π = 51.43;
the same as a depth-2 *blind* pocket → 64 − 2π = 57.72 (floor preserved). The
**Cutter overshoot** amount (5% default) is configurable; set it to 0 to disable.

### Make watertight (sew + heal)

The **Make watertight** export option (OCCT) sews all faces together, builds closed
solids and heals gaps within the **Sew tolerance**. It lets you fit the 6 planes of
a box (e.g. via segmentation) and export a single watertight box solid — the planes
are stitched into one closed shell. Crucially, it **reports the result honestly**:
if faces don't meet within tolerance it tells you how many free edges remain rather
than shipping a leaky file. (Verified: 6 loose planes → 1 closed solid, volume 8.0.)

### Optional OCCT kernel backend

For **booleans and merging fitted solids into one watertight body** (and a
kernel-grade writer), the exporter can use OpenCASCADE if it's installed. It is
*not* bundled:

- If no binding is found, the panel shows an **Install OCCT** button — one click
  runs `pip install --target` into the add-on's user folder (≈100 MB, no admin
  rights, survives Blender updates) and adds it to `sys.path`.
- Once present, the export dialog's **Backend** can be set to *OCCT* (or *Auto*),
  and **Merge into one solid** fuses overlapping primitives into a single body.
- Without it, everything still works via the pure-Python writer.
- The OCCT path writes a real `AP242_MANAGED_MODEL_BASED_3D_ENGINEERING_MIM_LF`
  file (verified by round-trip in `tests/test_occ_export.py`).

## How the fitting works

| Primitive | Method |
|-----------|--------|
| Plane     | SVD of centred points; normal = least-spread direction |
| Box       | Orientation by clustering face normals into 3 axes; extents from the vertex span. Recovers rotated boxes; rejects non-box selections by normal agreement |
| Sphere    | Algebraic (linear) least squares for centre + radius |
| Cylinder  | Axis from SVD of face normals (they avoid the axis), then a 2D Kåsa circle fit in the perpendicular plane |
| Cone      | Apex from the linear condition `(p − apex)·n = 0`; axis from the plane the normals trace; half-angle from radial-vs-axial slope |
| Torus     | Axis seeded by PCA + local angular refinement; centre from the region centroid; major/minor radii by linear least squares per candidate axis |
| Extrude   | Axis from the face-normal ∥/⊥ criterion (candidates tried in score order); profile from side faces ruled along the axis, chained into a closed loop and segmented into lines + arcs by seed-and-grow circle fitting |

Using **face normals** (available in Edit Mode) lets us solve the axis directly,
avoiding the brute-force orientation search the original Fusion add-in needs.

## Testing the core

The fitting math is pure NumPy and runs without Blender:

```bash
cd reverse_mesh
python3 tests/test_fitting.py
```

## Packaging / install

Build an installable extension `.zip` with Blender itself:

```bash
./build.sh           # or: blender --command extension build --source-dir reverse_mesh
```

Then in Blender: **Edit ▸ Preferences ▸ Add-ons ▸ Install from Disk** (or drag the
zip into the window) and enable **Reverse — Mesh to Parametric**.

## Roadmap

- **Tier 1 (this):** mesh region → clean analytic primitive (plane/cylinder/cone/sphere/torus). ✅
- **Tier 1 export:** pure-Python **AP242 STEP** of analytic solids. ✅
- **Tier 1.5 export (optional):** OCCT kernel backend — install-on-demand; merges
  solids into one watertight body and **booleans (Add/Subtract) to cut holes**. ✅
- **Next:** in-place region replacement; trim/extent refinement; partial-torus
  (fillet) robustness; per-feature colour via XCAF; intersect/section ops.
- **Tier 2:** detect extrude/revolve patterns → recover editable sketches.
