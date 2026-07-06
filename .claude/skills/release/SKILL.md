---
name: release
description: Cut a release of the reverse_mesh extension — bump the version in blender_manifest.toml, run the full test matrix, build the extension zip, verify its contents, then tag and create the GitHub release.
---

# Release the extension

Target version: `$ARGUMENTS` (X.Y.Z). If not given, propose the next version
from the changes since the last tag (features → minor bump, fixes only → patch)
and confirm with the user before proceeding.

## 1. Preconditions

- Working tree clean, on `main` (or a release branch about to merge to main).
- `git log $(git describe --tags --abbrev=0)..HEAD --oneline` — review what is
  going out; use it for the release notes.

## 2. Version bump

- `version = "X.Y.Z"` in `reverse_mesh/blender_manifest.toml` is the **single
  source of truth** — no other file carries the version.
- Commit style matches history: `Bump extension version to X.Y.Z`.

## 3. Test gate

Run the full matrix per the `test-all` skill (all three tiers). Do not release
on a skipped Tier 2/3 without telling the user which tiers did not run.

## 4. Build and verify the zip

```bash
reverse_mesh/build.sh
```

(wraps `blender --command extension build --source-dir reverse_mesh
--output-dir dist`; requires Blender 4.2+ on PATH). Then:

```bash
unzip -l dist/reverse_mesh-X.Y.Z.zip
```

- Confirm the zip name carries the new version.
- Confirm `tests/`, `__pycache__/`, `*.pyc`, `.git` are **absent** (manifest
  `[build].paths_exclude_pattern`).
- Confirm `blender_manifest.toml` and all `.py` modules are present.

## 5. Tag and publish

```bash
git tag vX.Y.Z
git push origin main --tags
gh release create vX.Y.Z dist/reverse_mesh-X.Y.Z.zip --title "vX.Y.Z" --notes "<notes>"
```

- Release notes: summarise the commit log since the previous tag, grouped as
  Features / Fixes; plain prose, user-facing.
- **Never** include AI attribution ("Generated with Claude", Co-Authored-By,
  etc.) in the commit, tag, or release text.

## 6. Aftercare

Verify the release page shows the asset (`gh release view vX.Y.Z`) and that the
README badges will resolve (release badge tracks the latest tag).
