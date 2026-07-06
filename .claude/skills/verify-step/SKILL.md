---
name: verify-step
description: Validate a STEP file the project's way — OCCT round-trip, BRepCheck validity, solid/face counts, and volume/area against analytic expectations. Use on any exported .step file and after touching step_export.py or occ_export.py.
---

# Verify a STEP file

Validate `$ARGUMENTS` (a `.step` path; if none given, run after-export checks on
the relevant test fixtures in `reverse_mesh/tests/*.step`).

**Why this ritual exists (from ERRORS.md):** a `CONICAL_SURFACE` defect passed
topological validity while the geometry was wrong (frustum volume 139 vs 68),
and an unwrapped `OPEN_SHELL` was silently dropped by importers while the file
stayed "valid". Validity alone proves nothing — always check counts AND
volume/area.

## 1. OCCT round-trip (the core check)

Use `.occ-venv/bin/python` (recreate via
`python3 -m venv .occ-venv && .occ-venv/bin/pip install cadquery-ocp numpy` if
missing). Reference harness: `reverse_mesh/tests/test_occ_export.py` — reuse
its patterns rather than inventing new ones.

Write a short script (scratchpad) that:

1. Reads the file with `STEPControl_Reader`, transfers all roots.
2. Runs `BRepCheck_Analyzer(shape).IsValid()` — must be True.
3. Counts entities with `TopExp_Explorer` (`TopAbs_SOLID`, `TopAbs_FACE`) and
   compares to what the source geometry should produce (e.g. a drilled box is
   1 solid / 7 faces). A dropped shell shows up here, not in validity.
4. Computes mass properties: `BRepGProp.VolumeProperties_s` and
   `SurfaceProperties_s`; compare volume/area to the analytic expectation from
   the primitive parameters (e.g. 4×4×4 box with an r=1 through-hole:
   64 − 4π ≈ 51.43). Tolerance ~1e-6 relative for clean primitives.
5. Reports open/free edges for anything claimed watertight.

## 2. File-level checks (no OCCT needed)

- AP242 header present: `ISO-10303-21` and
  `AP242_MANAGED_MODEL_BASED_3D_ENGINEERING_MIM_LF`.
- **Unit scale:** grep `CYLINDRICAL_SURFACE` entities and check the radii are
  in the expected millimetre magnitudes — a silent ×1000 scene-unit scaling bug
  is a logged defect (`ERRORS.md`). A 10 mm cylinder must not read 0.01 or
  10000.
- For subtract features: the cutter must NOT appear as an extra additive solid
  (another logged defect of the pure-Python writer).

## 3. Both backends

If the change touched export code, verify BOTH writers: the pure-Python
`step_export.build_step` output and the OCCT-native `occ_export` output, since
they are independent implementations validated by the same round-trip.

## Reporting

State per file: valid?, solids/faces found vs expected, volume found vs
expected (with the analytic formula used), open edges, unit sanity. A failure
in any one of these is a failure overall.
