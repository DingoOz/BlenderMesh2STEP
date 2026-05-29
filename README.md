# BlenderMesh2STEP

Reverse-engineer Blender meshes into clean, analytic **CAD primitives** and export
them as **STEP AP242** — a Blender 4.2+ extension.

It fits exact analytic surfaces (plane, box, cylinder, cone, sphere, torus) to
selected regions of a mesh, in the semi-automatic, human-in-the-loop style of the
[Reverse](https://github.com/nico-schluter/Reverse) Fusion 360 add-in, then writes
real analytic B-rep solids to STEP that open as true solids in FreeCAD and other
CAD tools.

> A mesh has thrown away the intent we want back — a 64-sided prism and a cylinder
> are identical triangles. Rather than guess, you tell the tool "these faces are a
> cylinder" and it recovers the exact analytic surface by least squares, typically
> to machine precision on clean, Blender-authored meshes.

## Features

- **Fits 6 analytic primitives**: plane, oriented box, cylinder, cone, sphere, torus.
- **Auto-detect** with normal-based disambiguation and an Occam tie-break.
- **Region segmentation** by crease angle (a cube → 6 planes; a cylinder → side + 2 caps).
- **Oriented box** reconstruction from planar faces (recovers rotation).
- **STEP AP242 export**, pure Python, zero dependencies — genuine analytic surfaces
  as valid `MANIFOLD_SOLID_BREP` solids, assembled with units and per-feature colour.
- **Optional OCCT kernel backend** (install-on-demand): merge solids into one
  watertight body and **boolean Add/Subtract** to reconstruct drilled/pocketed parts.

## Install

1. Download `reverse_mesh-*.zip` from the [Releases](../../releases) page (or build it,
   see below).
2. In Blender 4.2+: **Edit ▸ Preferences ▸ Add-ons ▸ Install from Disk** → pick the zip,
   then enable **Reverse — Mesh to Parametric**.

## Usage

1. Select a mesh and enter **Edit Mode**; open the **Reverse** tab in the sidebar (`N`).
2. Pick a primitive (or *Auto-detect*) and a **Role** (Add / Subtract).
3. Select the faces of one feature and click **Fit Primitive to Selection**. Repeat per feature.
4. **Export STEP (AP242)** — choose the *OCCT* backend for merged/boolean solids.

Full documentation: [`reverse_mesh/README.md`](reverse_mesh/README.md).

## Build from source

```bash
blender --command extension build --source-dir reverse_mesh --output-dir dist
```

## Tests

```bash
python3 reverse_mesh/tests/test_fitting.py            # fitting core (no Blender)
python3 reverse_mesh/tests/test_step.py               # STEP writer (no Blender)
blender --background --python reverse_mesh/tests/blender_smoke.py   # integration
```

## How it works / design

See [`mesh-to-parametric-plan.md`](mesh-to-parametric-plan.md) for the background and
the tiered design (geometry recovery vs. intent recovery), and
[`reverse_mesh/README.md`](reverse_mesh/README.md) for the per-primitive fitting methods.

## License

GPL-3.0-or-later.
