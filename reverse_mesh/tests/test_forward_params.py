# SPDX-License-Identifier: GPL-3.0-or-later
"""Validate the forward-build param schemas without Blender.

Every kind in ``forward.BUILD_KINDS`` must produce a param dict carrying the
exact keys the exporters' schema (operators._PARAM_KINDS / step_export) reads,
and an exact (rms=0) FitResult with a human summary.

    python3 reverse_mesh/tests/test_forward_params.py
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import forward  # noqa: E402

# Mirrors operators._PARAM_KINDS for the forward-buildable kinds (operators.py
# imports bpy, so the expectation is duplicated here rather than imported).
EXPECTED_KEYS = {
    "BOX": {"center", "ax", "ay", "az", "hx", "hy", "hz"},
    "CYLINDER": {"base", "axis", "radius", "height"},
    "CONE": {"base", "axis", "radius1", "radius2", "height", "half_angle"},
    "SPHERE": {"center", "radius"},
    "TORUS": {"center", "axis", "major_radius", "minor_radius"},
}

DIMS = {
    "BOX": {"hx": 1.0, "hy": 2.0, "hz": 3.0},
    "CYLINDER": {"radius": 2.0, "height": 6.0},
    "CONE": {"radius1": 3.0, "radius2": 1.0, "height": 4.0},
    "SPHERE": {"radius": 2.5},
    "TORUS": {"major_radius": 4.0, "minor_radius": 1.0},
}


def fail(msg):
    print(f"[FAIL] {msg}")
    sys.exit(1)


def main():
    if set(forward.BUILD_KINDS) != set(EXPECTED_KEYS):
        fail(f"BUILD_KINDS {forward.BUILD_KINDS} != expected {set(EXPECTED_KEYS)}")
    loc = (10.0, 20.0, 30.0)
    for kind in forward.BUILD_KINDS:
        params = forward.make_params(kind, DIMS[kind], loc)
        if set(params) != EXPECTED_KEYS[kind]:
            fail(f"{kind}: params keys {set(params)} != {EXPECTED_KEYS[kind]}")
        # Every PARAM_FIELDS entry must exist in the produced params.
        for key, _label in forward.PARAM_FIELDS[kind]:
            if key not in params:
                fail(f"{kind}: editable field '{key}' missing from params")
        result = forward.make_result(kind, params)
        if result.rms != 0.0 or result.max_error != 0.0:
            fail(f"{kind}: built primitive must be exact (rms=0)")
        if not result.summary:
            fail(f"{kind}: empty summary")
        print(f"[ok] {kind}: {result.summary}")

    # Cone: half_angle consistent with its radii, base offset to centre the body.
    p = forward.make_params("CONE", DIMS["CONE"], loc)
    want = math.atan(abs(1.0 - 3.0) / 4.0)
    if abs(p["half_angle"] - want) > 1e-12:
        fail(f"cone half_angle {p['half_angle']} != {want}")
    if abs(p["base"][2] - (30.0 - 2.0)) > 1e-12:
        fail(f"cone base z {p['base'][2]} not offset by -h/2")
    # Cylinder: base IS the body centre (build/_cylinder spans ±h/2 around it).
    p = forward.make_params("CYLINDER", DIMS["CYLINDER"], loc)
    if tuple(p["base"]) != loc:
        fail(f"cylinder base {p['base']} should equal the drop location")
    print("[ok] placement conventions (cone base offset, cylinder centred)")
    print("ALL FORWARD PARAM TESTS PASSED")


if __name__ == "__main__":
    main()
