#!/usr/bin/env bash
# Build an installable Blender extension .zip from this source directory.
# Requires Blender 4.2+ on PATH. Output lands in ../dist/.
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="$(dirname "$SRC_DIR")/dist"
mkdir -p "$OUT_DIR"

if ! command -v blender >/dev/null 2>&1; then
  echo "error: 'blender' not found on PATH (need Blender 4.2+)." >&2
  echo "Install the extension manually instead: zip the '$(basename "$SRC_DIR")' folder" >&2
  echo "and use Preferences > Add-ons > Install from Disk." >&2
  exit 1
fi

blender --command extension build \
  --source-dir "$SRC_DIR" \
  --output-dir "$OUT_DIR"

echo "Built extension into: $OUT_DIR"
