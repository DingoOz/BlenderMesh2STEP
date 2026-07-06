---
name: test-all
description: Run the full BlenderMesh2STEP test matrix — standalone NumPy tests, OCCT kernel round-trip, and Blender headless smoke tests. Use before committing, releasing, or after touching fitting/export code.
---

# Run the full test matrix

Run all tiers from the repo root. CI (`.github/workflows/tests.yml`) only covers
Tier 1 — Tiers 2 and 3 are **local-only** and must be run by hand before any
release or export-related change.

## Tier 1 — Standalone Python (pure NumPy, no Blender)

```bash
python3 reverse_mesh/tests/test_fitting.py
python3 reverse_mesh/tests/test_step.py
python3 reverse_mesh/tests/test_decompose.py
python3 reverse_mesh/tests/test_solidfit.py
python3 reverse_mesh/tests/test_forward_params.py
python3 -m compileall -q reverse_mesh
```

All five must print their pass summaries and compileall must be silent.

## Tier 2 — OCCT kernel round-trip (local-only)

```bash
.occ-venv/bin/python reverse_mesh/tests/test_occ_export.py
```

- The test skips cleanly if no OCCT binding is importable — a skip is NOT a pass
  for export changes; treat it as a missing tier and say so.
- If `.occ-venv/` is missing, recreate it:
  `python3 -m venv .occ-venv && .occ-venv/bin/pip install cadquery-ocp numpy`
  (it is gitignored; large download).

## Tier 3 — Blender headless integration (local-only)

```bash
blender --background --python reverse_mesh/tests/blender_smoke.py
blender --background --python reverse_mesh/tests/test_forward.py
```

- **Known harmless noise:** an `unregister_class ... missing bl_rna` traceback
  at startup comes from a stale installed copy under
  `~/.config/blender/*/extensions/user_default/reverse_mesh/`, NOT from the
  repo under test (the smoke test imports the repo via `sys.path`). Ignore it.
- **Success criterion:** the `ALL ... CHECKS PASSED` line. Judge pass/fail by
  that line and the exit code, not by the absence of tracebacks.
- Local `blender` is a snap that auto-updates (5.x as of mid-2026) while the
  extension targets 4.2+ (`blender_manifest.toml`). If a failure looks like a
  bpy API change, check whether it is a 5.x-only behaviour before "fixing" code
  that is correct on 4.2.

## Reporting

Report each tier's result separately. If any tier was skipped (no venv, no
blender on PATH), state that explicitly rather than reporting overall success.
