---
name: new-primitive
description: Checklist for adding a new analytic surface/primitive type to reverse_mesh — fitter, AUTO-detect gates, STEP emission, OCCT backend, forward build, and the full test set. Use when adding or substantially reworking a primitive.
---

# Add a new primitive type

Adding a surface type touches a fixed set of places. Work through them in
order; before starting, re-read the `ERRORS.md` entries whose **File(s)** match
the files below — most defects in this repo are recurrences of logged patterns.

## 1. Fitter — `reverse_mesh/fitting/primitives.py`

- Take a `Region` (paired vertices + face-centroids/face-normals). **Never**
  element-wise combine a per-vertex array with a per-face array — they have
  different lengths by construction (logged defect).
- Acceptance must use **normals, not just point residual**: points lying on a
  surface is not evidence of the shape (sphere-vs-cylinder and box-cap defects).
- Avoid PCA/covariance eigenvectors where the expected structure is symmetric
  (equal eigenvalues → arbitrary axes; the rotated-cube defect). Cluster or
  search discrete directions instead.
- Guard against degenerate fits (huge-radius sphere/cylinder approximating a
  plane) — cap or penalise implausible parameters.
- Report RMS; clean Blender-authored meshes should land near 1e-8.

## 2. AUTO detection — same file

Register the fitter in the AUTO candidate set. It must participate in:
- the **normal-agreement gate** (predicted vs actual face normals — extend
  `predicted_normals`/`normal_alignment`; a missing branch there is a logged
  defect), and
- the **Occam tie-break** (simpler primitive wins among essentially-exact
  fits) — decide where the new type sits in that ordering.

## 3. STEP emission — `reverse_mesh/step_export.py`

- Follow STEP axis/orientation conventions exactly (e.g. `CONICAL_SURFACE`
  requires `semi_angle > 0` with the placement axis pointing toward increasing
  radius — getting this wrong passed validity but broke volume; logged defect).
- Representation items must be *model* entities (`SHELL_BASED_SURFACE_MODEL`,
  solid models, geometric sets) — never a raw shell (logged defect: importers
  silently drop it).
- Respect scene unit scaling (`units.py`); dimensions are written in mm.
- Support the Add/Subtract role if the primitive can act as a cutter — cutters
  must not be emitted as extra additive solids (logged defect).

## 4. OCCT backend — `reverse_mesh/occ_export.py`

Mirror the new surface in the kernel-based exporter so both backends stay
feature-equivalent.

## 5. Forward build — `reverse_mesh/forward.py` + `reverse_mesh/build.py`

Add the canonical-frame mesh generator in `build.py` (note: `build.py` is a
source module, not the packaging script — that is `build.sh`) and the
parameter-carrying forward object in `forward.py`.

## 6. Tests

- Fit accuracy + degenerate/ambiguity cases: `reverse_mesh/tests/test_fitting.py`
- Writer output: `reverse_mesh/tests/test_step.py`
- OCCT round-trip with an analytic volume/area assertion:
  `reverse_mesh/tests/test_occ_export.py`
- Forward params: `reverse_mesh/tests/test_forward_params.py`
- Integration coverage in `reverse_mesh/tests/blender_smoke.py`

## 7. Finish

- Run the full matrix (`test-all` skill) and validate an exported sample with
  the `verify-step` skill.
- Update the primitive table in `README.md` (and `reverse_mesh/README.md`).
- If you hit and fixed a defect on the way, log it in `ERRORS.md` per the
  mandated format.
