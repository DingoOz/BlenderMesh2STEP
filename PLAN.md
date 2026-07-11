# BlenderMesh2STEP — Development Plan

*Last updated: 2026-07-11 (v0.11.0).*

This plan captures where the project goes next. Background and the original
tier analysis live in [mesh-to-parametric-plan.md](mesh-to-parametric-plan.md);
this file is the actionable roadmap.

## Where we are

Tier 1 (mesh → clean B-rep solid) and Tier 1.5 (optional OCCT backend with
real Add/Subtract booleans, sewing, and validation reports) are **done**:

- Least-squares fitters for plane, box, cylinder, cone, sphere, torus and
  fillet strips, landing at ~1e-8 RMS on clean Blender-authored meshes.
- Pure-Python AP242 writer (zero dependencies) emitting true analytic solids.
- OCCT backend (`cadquery-ocp` / pythonocc, install-on-demand) with fuse/cut
  booleans, through/blind holes, counterbore/countersink, sew-to-watertight,
  and per-solid validity/volume reporting.
- Forward "Build — STEP Primitives" panel: parametric-by-construction solids.
- Feature stack UI, pattern propagation, deviation heatmap, PMI sidecar.

## Guiding principles

1. **The user supplies intent; the tool supplies precision.** Semi-automatic,
   human-in-the-loop fitting is the differentiator. Automation stays a
   convenience the user can correct, never a promise.
2. **Manufacturing correctness beats convenience.** A STEP file with a filled
   hole or an open shell is worse than an error message. Anything that cannot
   be represented correctly must be flagged loudly, not silently degraded.
3. **OCCT is the product; the pure-Python writer is the portable fallback.**
   Booleans, watertightness and validation only exist on the kernel path, so
   the UX should treat "no OCCT" as a degraded mode.
4. **Stay out of research territory.** No Tier 3 feature-tree/intent ML, no
   scan/NURBS freeform reconstruction. Those are different products.

## Near-term roadmap (ordered)

### Phase 1 — OCCT-first packaging + CI for the kernel path
*Why first: every High-severity bug so far (silent ×1000 units, cutters
written as filled material) lived in the layers CI does not exercise.*

- Treat missing OCCT as a degraded mode in the UI: persistent notice in the
  Reverse and Export panels, boolean/watertight options clearly labelled as
  requiring the kernel, one-click install kept front and centre.
- Investigate bundling per-platform `cadquery-ocp` wheels via
  `blender_manifest.toml` `wheels = [...]` (subject to the
  extensions.blender.org size ceiling); otherwise keep install-on-demand.
- CI: add a plain-Python job that installs `cadquery-ocp` and runs
  `test_occ_export.py`; add a headless-Blender job for `blender_smoke.py`
  and `test_forward.py`. CI currently gates only the pure-NumPy core.

### Phase 2 — Extruded-profile solids (the big coverage win)
*The archetypal Blender-authored part is a prism: a planar outline of lines
and arcs extruded along an axis. Today those fall through to faceted
`MESH_PATCH`, defeating the point of the tool.*

- Reverse: detect prism regions (two parallel planar caps + side faces
  perpendicular to one axis), project the boundary, fit line/arc segments,
  emit profile + direction + length.
- Export: OCCT wire → face → `BRepPrimAPI_MakePrism`. Pure-Python writer
  support for straight-segment profiles where feasible.
- Forward: "Extrude" entry in the Build panel (profile presets + N-gon).
- AUTO-detect gate, full fitter/export/round-trip test set
  (see the `new-primitive` skill checklist).

### Phase 3 — Fillets and chamfers as real blends
- On the OCCT path, apply recognized FILLET features (radius + edge) as
  `BRepFilletAPI_MakeFillet` blends on the fused solid instead of emitting
  open trimmed patches — the body stays watertight and the blend is editable
  downstream.
- Add chamfer recognition (conical/planar strip → `MakeChamfer`).

### Phase 4 — Revolve recovery
- Detect rotational symmetry about an axis, fit the half-profile
  (lines + arcs), emit `BRepPrimAPI_MakeRevol` solids.
- Forward-build revolve primitive. Shares profile-fitting machinery with
  Phase 2.

### Phase 5 — Multi-body and XCAF
- Named multi-body STEP output with real assembly structure (today everything
  collapses into a single `PRODUCT`).
- Per-feature/body colours via `STEPCAFControl_Writer` (XCAF) on the OCCT
  path so colours survive into FreeCAD/Fusion.

### Phase 6 — Richer volumetric solid-fit
- Extend `fitting/solidfit.py` beyond sphere/cylinder/box: inscribed cones
  and tori, finer SDF sampling, non-PCA cylinder axes.

## Future directions (captured, not yet scheduled)

### A. Connect CAD artefacts to Blender simulation
Bridge the parametric feature stack with Blender's simulation systems so the
same object drives both manufacture and simulation:

- **Rigid-body / physics:** derive exact collision shapes (analytic primitives,
  convex prisms) and true volume/mass properties from the fitted features
  instead of the display mesh; write mass/inertia from material density.
- **Round-trip:** re-import the exported STEP (via the OCCT backend) as a
  tessellated preview so the simulated geometry is provably the manufactured
  geometry.
- **Geometry Nodes:** expose feature parameters (radii, lengths, hole
  positions) as node inputs so simulations and motion studies react when a
  dimension changes.
- **Tolerance studies:** perturb feature parameters within PMI tolerances and
  re-run a sim (fit/clearance checks, e.g. does the pin still enter the bore).

### B. CNC toolpath planning (optional module)
An opt-in CAM layer that consumes the feature stack — which already knows
"this is a Ø8 through-hole", "this is a pocket", "this face is planar" —
rather than re-discovering features from geometry:

- **Basic (2.5D first):** drilling cycles from cylinder/hole features
  (spot, peck, tap from `thread_spec`), face-milling planar regions,
  contour/pocket milling of extruded profiles with tool-radius offsetting,
  simple depth stepping. Output as G-code (GRBL/LinuxCNC dialects) and/or
  FreeCAD Path job export.
- **Complex (later):** 3-axis surface clearing for non-planar analytic
  regions, rest-machining, tool libraries, feeds/speeds from material
  presets, stock definition from the bounding solid, toolpath visualisation
  as Blender curves/Grease Pencil, collision checks against fixtures.
- **Design stance:** feature-based CAM (the hard part of CAM — feature
  recognition — is exactly what this tool already produces). Start with
  post-processors for hobby-class machines where Blender users actually are.

## Non-goals

- Tier 3 feature-tree / boolean-history inference (ML research territory).
- Scan data, noisy point clouds, NURBS freeform reconstruction.
- Becoming a general CAD editor inside Blender — the output is meant to be
  finished in FreeCAD/Fusion/SolidWorks.

## Tracking

Each phase lands as a `feature/<name>` branch and PR into `main`, with the
full test matrix (`test-all` skill) run before merge, and the user guide
(`docs/user-guide.tex`) updated in the same PR as the feature it documents.
