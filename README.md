# BlenderMesh2STEP

**Turn dumb triangle meshes back into real CAD.** A Blender 4.2+ extension that
fits exact analytic surfaces to your mesh and exports genuine **STEP AP242**
solids — the kind FreeCAD, Fusion, SolidWorks and Onshape open as editable,
measurable, manufacturable geometry.

[![tests](https://github.com/DingoOz/BlenderMesh2STEP/actions/workflows/tests.yml/badge.svg)](https://github.com/DingoOz/BlenderMesh2STEP/actions/workflows/tests.yml)
[![Release](https://img.shields.io/github/v/release/DingoOz/BlenderMesh2STEP)](https://github.com/DingoOz/BlenderMesh2STEP/releases)
[![Blender](https://img.shields.io/badge/Blender-4.2%2B-orange)](https://www.blender.org/)
[![License](https://img.shields.io/badge/license-GPL--3.0--or--later-blue)](LICENSE)

---

## The problem it solves

You modelled a part in Blender. Now a machine shop, a CAD engineer, or a slicer
wants a **real solid** — a STEP file with actual cylinders and planes, not a
soup of triangles. Exporting your mesh as STL or a tessellated STEP gives them a
faceted blob they can't measure, fillet, or edit.

A mesh has thrown away the *intent* you want back: a 20 mm cylinder and a
64-sided prism are **identical triangles**. There's no button that can reliably
guess what a mesh "meant to be."

**BlenderMesh2STEP doesn't guess — you point, it reconstructs.** You tell it
"these faces are a cylinder," and it recovers the exact analytic surface by
least-squares fitting, typically **to machine precision** on clean,
Blender-authored meshes. The result is a true B-rep solid, not an approximation.

```
   Blender mesh                BlenderMesh2STEP              STEP AP242 solid
  (triangles, no intent)   ──►  fit + reconstruct   ──►   (real cylinders, planes,
   64-gon "cylinder"             you confirm intent          holes, dimensions)
```

## What you can actually do with it

- **Convert a printed/modelled part to editable CAD.** Select a face → get a
  clean plane. Select a curved wall → get an exact cylinder with a real radius.
  Export STEP and open it in FreeCAD as a true solid you can dimension and modify.
- **Reconstruct boxes and cuboids** — even rotated ones — from their flat faces,
  instead of an averaged mess. One click turns a cube into a proper box solid.
- **Recover edge fillets** as real rounds. Select a fillet strip → get the exact
  blend radius and arc — and on the OCCT path the fillet is applied as a **true
  rounded edge on the solid** (watertight, CAD-editable), not a loose patch.
- **Drill holes and cut pockets with booleans.** Tag a fitted cylinder as
  *Subtract* and the exporter carves it out of the base body — reconstructing a
  drilled, pocketed part as a single watertight solid with real holes. Add a
  **counterbore or countersink** preset and the recess is cut too.
- **Select a whole surface in one click** ("Select Similar" grows from one face to
  the entire wall, stopping at sharp edges), or **split an object into its surfaces
  automatically** — a cube into 6 planes, a cylinder into its side + 2 caps.
- **Fit one hole, get the rest.** *Propagate Pattern* finds every matching hole in
  the mesh (bolt circles, arrays, mirrors) and fits them with the seed's settings.
- **See exactly how good each fit is.** A green→red **deviation heatmap** colours
  the selected faces so a stray chamfer or mis-pick is obvious, not buried in an
  average. Optional **outlier rejection** (RANSAC) survives a slightly dirty selection.
- **Edit non-destructively.** Every fit lands in a **feature stack** you can
  reorder, re-fit, delete, and reload across sessions — toggle Add/Subtract, snap
  dimensions to nice numbers, or tag a hole with a thread (e.g. `M8x1.25`).
- **Measure and verify.** Every fit reports its RMS error; the export gives a
  **per-solid validation report** (volume, validity, open edges), and you can write
  a **PMI dimension sidecar** (CSV/JSON) or embed **semantic AP242 dimensions**.

## Fits the full analytic surface set

| Primitive | What it recovers | How |
|-----------|------------------|-----|
| **Plane** | flat faces, with extent | SVD — normal = least-spread direction |
| **Box** | oriented cuboids (rotation recovered) | clusters face normals into 3 axes |
| **Cylinder** | axis, radius, height | axis from normals + Kåsa circle fit |
| **Cone** | apex, half-angle, radii (incl. frustums) | linear apex condition `(p−apex)·n = 0` |
| **Sphere** | centre, radius | algebraic least squares |
| **Torus** | axis, major + minor radius | PCA seed + angular refinement |
| **Fillet** | edge round → partial cylinder (radius + arc) | circle fit + angular-extent recovery |
| **Extrude** | prism: planar line/arc profile + height | axis from normals, profile from ruled side faces |
| **Auto** | picks the best fit | normal agreement + Occam tie-break |

On clean meshes these fits land at **~1e-8** RMS — effectively exact.

## 60-second workflow

1. Select your mesh, enter **Edit Mode**, open the **Reverse** tab in the sidebar (`N`).
2. Choose a primitive (or **Auto-detect**) and a **Role** (*Add* / *Subtract*).
3. Select the faces of one feature (or click one and **Select Similar**) →
   **Fit Primitive to Selection**. The fit lands in the feature stack; the optional
   heatmap shows how well it matches. Repeat per feature.
4. **Export STEP (AP242)** → open it in your CAD tool as a real solid, and read the
   validation report to confirm volumes and watertightness.

That's the semi-automatic, human-in-the-loop model proven by the
[Reverse](https://github.com/nico-schluter/Reverse) Fusion 360 add-in — robust,
because *you* supply the one thing a mesh can't: intent.

## Build with STEP primitives (forward modeling)

You don't have to start from a mesh. The **Build — STEP Primitives** panel adds
solids (box, cylinder, cone, sphere, torus) that are STEP-exportable *by
construction* — the exact analytic parameters live on the object, the viewport
mesh is just a preview.

1. In the **Reverse** tab, open **Build — STEP Primitives**, pick a primitive
   and a **Role** (*Add* / *Subtract*), then **Add Primitive** — it lands at the
   3D cursor and joins the same feature stack (tagged `[B]`).
2. With the object selected, edit its dimensions live in the panel — the mesh
   regenerates and the stored parameters stay exact. Move/rotate freely;
   uniform scale can be baked into the dimensions with one click.
3. Mix freely with reverse-fit features (e.g. a built cylinder as a *Subtract*
   hole through a fitted plate) and **Export STEP** as usual.

Guard rails instead of locks: if you edit the mesh in Edit Mode or apply a
non-uniform scale to a curved primitive, the panel and the export report flag
the drift — **Rebuild from Parameters** snaps the mesh back to the analytic
truth. (Note: undoing a dimension edit restores the number but not the mesh
until the next change — Rebuild fixes that too.)

## Two ways to export — both real STEP

**Pure Python (built in, zero dependencies).** Writes genuine analytic surfaces
(`CYLINDRICAL_SURFACE`, `TOROIDAL_SURFACE`, trimmed fillet patches, …) as valid
`MANIFOLD_SOLID_BREP` solids in a real **AP242** file, assembled with units and
per-feature colour. Optionally annotates threads and embeds **semantic PMI
dimensions** (`DIMENSIONAL_SIZE`). No kernel, no install — works the moment you
enable the add-on.

**OCCT kernel (optional, one-click install).** For **merging solids into one
watertight body**, **boolean Add/Subtract** (drilling through & blind holes,
cutting pockets, counterbores & countersinks), **auto-stitch** that unifies
abutting features into genuinely shared topology, and a **Make watertight**
sew-and-heal pass that stitches loose faces into a closed solid and tells you if
any boundary is still open. Every export comes with a **validation report** —
per-solid volume, kernel validity, and open-edge count.
If OpenCASCADE isn't present, the panel offers an **Install OCCT** button that
fetches it into the add-on's own folder — no admin rights, survives Blender
updates. Without it, everything else still works.

## Proven correct, not just plausible

Hand-authored B-rep topology usually *looks* right and silently fails in real CAD.
This one is validated against the OpenCASCADE kernel: every exported solid imports
as **topologically valid with the correct volume**, e.g.

- cylinder r2 × h6 → **75.40**, sphere r2.5 → **65.45**, torus → **222.07**
- box 4×4×4 drilled by an r1 cylinder → **64 − 4π = 51.43** ✅
- box drilled + counterbored (r1.5 × 1 deep) → **47.51** ✅
- two abutting boxes, auto-stitched → **one solid, 6 shared faces** (not two islands)
- 90° edge fillet → trimmed cylindrical patch, re-reads valid with exact area ✅

## Install

1. Download `reverse_mesh-*.zip` from the [**Releases**](../../releases) page.
2. Blender 4.2+: **Edit ▸ Preferences ▸ Add-ons ▸ Install from Disk** → pick the zip
   → enable **Reverse — Mesh to Parametric**.

Or build it yourself:

```bash
blender --command extension build --source-dir reverse_mesh --output-dir dist
```

## Tests

```bash
python3 reverse_mesh/tests/test_fitting.py     # fitting core (no Blender)
python3 reverse_mesh/tests/test_step.py        # STEP writer (no Blender)
python3 reverse_mesh/tests/test_forward_params.py  # forward-build schemas (no Blender)
blender --background --python reverse_mesh/tests/blender_smoke.py     # integration
blender --background --python reverse_mesh/tests/test_forward.py      # forward building
blender --background --python reverse_mesh/tests/test_occ_export.py   # OCCT kernel (if installed)
```

## Honest about the edges

This is **Tier 1** reverse engineering — geometry recovery, not full feature-tree
/ intent recovery (that's an unsolved ML research problem). It shines on clean,
prismatic, mechanical parts and is semi-automatic by design: the human picks
features so the tool never has to guess wrong. Organic/freeform shapes are out of
scope. See [`mesh-to-parametric-plan.md`](mesh-to-parametric-plan.md) for the full
design rationale and the tiered roadmap, and
[`reverse_mesh/README.md`](reverse_mesh/README.md) for per-primitive details.

## Roadmap

**Recently landed:** whole-mesh **solid decompose** (fills the volume with a union
of inscribed solids — a capsule → cylinder + 2 spheres, tessellation-independent,
nothing juts outside; export via OCCT boolean union) · whole-mesh surface
auto-decompose (one button: globally optimizes the whole part into a separate set
of primitives) · edge-fillet recovery with
trimmed-surface export · pattern propagation (fit one hole, find the rest) ·
auto-stitch into shared topology · counterbore/countersink presets · fit-quality
heatmap · RANSAC outlier rejection · non-destructive feature stack · dimension
snapping · validation report · thread tagging · PMI sidecar + semantic AP242
dimensions.

**Next:**

- In-place region replacement (stitch the clean primitive back into the mesh)
- Corner-blend (partial-torus) fillets, beyond the current edge-fillet case
- OCCT intersect/section ops and per-feature colour via XCAF
- Sketch + extrude/revolve recovery (Tier 2 — genuinely parametric output)

## License

[GPL-3.0-or-later](LICENSE). Inspired by [nico-schluter/Reverse](https://github.com/nico-schluter/Reverse).
